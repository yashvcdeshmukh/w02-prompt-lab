"""Day 1: instrument real Mistral calls through Ollama."""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from promptlab.config import PROJECT_ROOT, Settings
from promptlab.usage import CallRecord, append_record, compute_cost

CASE_IDS = ("E12", "E07", "E11")
PROMPT_ID = "baseline"
PROMPT_VERSION = "v0"
MAX_OUTPUT_TOKENS = 256
TRUNCATION_NUM_PREDICT = 8
REQUEST_TIMEOUT_SECONDS = 180.0
EVIDENCE_PATH = PROJECT_ROOT / "docs" / "day1-run.jsonl"


def load_extraction_sources() -> dict[str, str]:
    path = PROJECT_ROOT / "cases" / "extraction.jsonl"
    sources: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        payload: dict[str, Any] = json.loads(line)
        sources[str(payload["id"])] = str(payload["source"])
    return sources


def load_baseline_prompt() -> str:
    path = PROJECT_ROOT / "src" / "prompts" / "baseline.v0.md"
    return path.read_text(encoding="utf-8")


def generate(
    *,
    base_url: str,
    model_id: str,
    prompt: str,
    temperature: float,
    num_predict: int,
) -> tuple[dict[str, Any], int]:
    started = time.perf_counter()
    response = httpx.post(
        f"{base_url}/api/generate",
        json={
            "model": model_id,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
            },
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    return payload, latency_ms


def record_from_payload(
    *,
    run_id: str,
    model_id: str,
    case_id: str,
    temperature: float,
    max_output_tokens: int,
    payload: dict[str, Any],
    latency_ms: int,
    error_type: str | None,
) -> CallRecord:
    input_tokens = int(payload.get("prompt_eval_count") or 0)
    output_tokens = int(payload.get("eval_count") or 0)
    stop_reason = payload.get("done_reason")
    stop_reason_text = str(stop_reason) if stop_reason is not None else None
    response_text = payload.get("response")
    response_text_value = str(response_text) if response_text is not None else None
    return CallRecord(
        record_id=str(uuid.uuid4()),
        run_id=run_id,
        timestamp=datetime.now(UTC),
        provider="ollama",
        model_id=model_id,
        task="extraction",
        case_id=case_id,
        prompt_id=PROMPT_ID,
        prompt_version=PROMPT_VERSION,
        attempt=1,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=None,
        latency_ms=latency_ms,
        cost_usd=compute_cost(model_id, input_tokens, output_tokens),
        stop_reason=stop_reason_text,
        error_type=error_type,
        response_text=response_text_value,
    )


def main() -> None:
    settings = Settings.from_env()
    model = settings.models["mistral"]
    sources = load_extraction_sources()
    template = load_baseline_prompt()
    run_id = str(uuid.uuid4())
    temperature = 0.0

    truncation_prompt = template.replace("{document_text}", sources["E11"])
    truncated_payload, truncated_latency_ms = generate(
        base_url=settings.ollama_base_url,
        model_id=model.model_id,
        prompt=truncation_prompt,
        temperature=temperature,
        num_predict=TRUNCATION_NUM_PREDICT,
    )
    truncated_error: str | None = None
    if truncated_payload.get("done_reason") == "length":
        truncated_error = "TruncatedResponseError"
    truncated_record = record_from_payload(
        run_id=run_id,
        model_id=model.model_id,
        case_id="E11",
        temperature=temperature,
        max_output_tokens=TRUNCATION_NUM_PREDICT,
        payload=truncated_payload,
        latency_ms=truncated_latency_ms,
        error_type=truncated_error,
    )
    append_record(truncated_record, run_id)

    successful_records: list[CallRecord] = []
    for case_id in CASE_IDS:
        prompt = template.replace("{document_text}", sources[case_id])
        payload, latency_ms = generate(
            base_url=settings.ollama_base_url,
            model_id=model.model_id,
            prompt=prompt,
            temperature=temperature,
            num_predict=MAX_OUTPUT_TOKENS,
        )
        record = record_from_payload(
            run_id=run_id,
            model_id=model.model_id,
            case_id=case_id,
            temperature=temperature,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            payload=payload,
            latency_ms=latency_ms,
            error_type=None,
        )
        append_record(record, run_id)
        successful_records.append(record)

    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(
        "".join(record.model_dump_json() + "\n" for record in successful_records),
        encoding="utf-8",
    )
    print(f"run_id={run_id}")
    print(f"wrote {len(successful_records)} evidence records to {EVIDENCE_PATH}")
    print(
        "truncation demo:",
        truncated_record.stop_reason,
        truncated_record.error_type,
    )


if __name__ == "__main__":
    main()
