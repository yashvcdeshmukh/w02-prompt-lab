from __future__ import annotations

import random
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from promptlab.adapters.base import CompletionRequest, CompletionResult
from promptlab.config import Settings
from promptlab.errors import PermanentProviderError, TransientProviderError, TruncatedResponseError
from promptlab.usage import CallRecord, compute_cost

_TIMEOUT_SECONDS = 180.0
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 0.5


def _backoff_seconds(failed_attempt: int) -> float:
    return float(_BACKOFF_BASE_SECONDS * (2 ** (failed_attempt - 1)) + random.uniform(0.0, 0.25))


@dataclass(frozen=True)
class _RawCompletion:
    text: str
    input_tokens: int
    output_tokens: int
    stop_reason: str | None
    latency_ms: int


def _join_prompt(system: str, user_content: str) -> str:
    system_text = system.strip()
    if not system_text:
        return user_content
    return f"{system_text}\n\n{user_content}"


class OllamaAdapter:
    provider: str = "ollama"

    def __init__(self, *, model_id: str, base_url: str | None = None) -> None:
        self.model_id = model_id
        resolved_base_url = (
            base_url if base_url is not None else Settings.from_env().ollama_base_url
        )
        self._base_url = resolved_base_url.rstrip("/")

    def complete(self, request: CompletionRequest, run_id: str) -> CompletionResult:
        records: list[CallRecord] = []
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                raw = self._once(request)
            except TransientProviderError as exc:
                records.append(
                    self._record(
                        request,
                        run_id=run_id,
                        attempt=attempt,
                        text=None,
                        input_tokens=0,
                        output_tokens=0,
                        stop_reason=None,
                        latency_ms=exc.latency_ms,
                        error_type=TransientProviderError.__name__,
                    )
                )
                if attempt < _MAX_ATTEMPTS:
                    time.sleep(_backoff_seconds(attempt))
                    continue
                return CompletionResult(
                    succeeded=False,
                    text=None,
                    error_type=TransientProviderError.__name__,
                    records=records,
                )
            except PermanentProviderError as exc:
                records.append(
                    self._record(
                        request,
                        run_id=run_id,
                        attempt=attempt,
                        text=None,
                        input_tokens=0,
                        output_tokens=0,
                        stop_reason=None,
                        latency_ms=exc.latency_ms,
                        error_type=PermanentProviderError.__name__,
                    )
                )
                return CompletionResult(
                    succeeded=False,
                    text=None,
                    error_type=PermanentProviderError.__name__,
                    records=records,
                )
            except TruncatedResponseError as exc:
                records.append(
                    self._record(
                        request,
                        run_id=run_id,
                        attempt=attempt,
                        text=exc.text,
                        input_tokens=exc.input_tokens,
                        output_tokens=exc.output_tokens,
                        stop_reason=exc.stop_reason,
                        latency_ms=exc.latency_ms,
                        error_type=TruncatedResponseError.__name__,
                    )
                )
                return CompletionResult(
                    succeeded=False,
                    text=exc.text,
                    error_type=TruncatedResponseError.__name__,
                    records=records,
                )

            records.append(
                self._record(
                    request,
                    run_id=run_id,
                    attempt=attempt,
                    text=raw.text,
                    input_tokens=raw.input_tokens,
                    output_tokens=raw.output_tokens,
                    stop_reason=raw.stop_reason,
                    latency_ms=raw.latency_ms,
                    error_type=None,
                )
            )
            return CompletionResult(
                succeeded=True,
                text=raw.text,
                error_type=None,
                records=records,
            )

        return CompletionResult(
            succeeded=False,
            text=None,
            error_type=TransientProviderError.__name__,
            records=records,
        )

    def _once(self, request: CompletionRequest) -> _RawCompletion:
        started = time.perf_counter()
        try:
            response = httpx.post(
                f"{self._base_url}/api/generate",
                json={
                    "model": self.model_id,
                    "prompt": _join_prompt(request.system, request.user_content),
                    "stream": False,
                    "options": {
                        "temperature": request.temperature,
                        "num_predict": request.max_output_tokens,
                    },
                },
                timeout=_TIMEOUT_SECONDS,
            )
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            raise TransientProviderError(str(exc), latency_ms=latency_ms) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 500:
            raise TransientProviderError(
                f"Ollama HTTP {response.status_code}",
                latency_ms=latency_ms,
            )
        if response.status_code >= 400:
            raise PermanentProviderError(
                f"Ollama HTTP {response.status_code}",
                latency_ms=latency_ms,
            )

        payload: dict[str, Any] = response.json()
        stop_reason = payload.get("done_reason")
        text_value = payload.get("response")
        raw = _RawCompletion(
            text=str(text_value) if text_value is not None else "",
            input_tokens=int(payload.get("prompt_eval_count") or 0),
            output_tokens=int(payload.get("eval_count") or 0),
            stop_reason=str(stop_reason) if stop_reason is not None else None,
            latency_ms=latency_ms,
        )
        if raw.stop_reason == "length":
            raise TruncatedResponseError(
                text=raw.text,
                input_tokens=raw.input_tokens,
                output_tokens=raw.output_tokens,
                latency_ms=raw.latency_ms,
                stop_reason=raw.stop_reason,
            )
        return raw

    def _record(
        self,
        request: CompletionRequest,
        *,
        run_id: str,
        attempt: int,
        text: str | None,
        input_tokens: int,
        output_tokens: int,
        stop_reason: str | None,
        latency_ms: int,
        error_type: str | None,
    ) -> CallRecord:
        return CallRecord(
            record_id=str(uuid.uuid4()),
            run_id=run_id,
            timestamp=datetime.now(UTC),
            provider="ollama",
            model_id=self.model_id,
            task=request.task,
            case_id=request.case_id,
            prompt_id=request.prompt_id,
            prompt_version=request.prompt_version,
            attempt=attempt,
            temperature=request.temperature,
            max_output_tokens=request.max_output_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=None,
            latency_ms=latency_ms,
            cost_usd=compute_cost(self.model_id, input_tokens, output_tokens),
            stop_reason=stop_reason,
            error_type=error_type,
            response_text=text,
        )
