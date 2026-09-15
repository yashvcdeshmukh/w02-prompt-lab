"""Day 4: compare two triage prompt versions under one controlled run."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from promptlab.adapters.base import CompletionRequest, CompletionResult
from promptlab.adapters.ollama import OllamaAdapter
from promptlab.config import PROJECT_ROOT, Settings
from promptlab.errors import PermanentProviderError, TransientProviderError
from promptlab.prompts import load, render_user
from promptlab.records import OutputRecord, Record, UsageRecord
from promptlab.schemas import TriageOutput, TriageOutputWithAnalysis
from promptlab.scoring import score_triage
from promptlab.structured import complete_structured
from promptlab.usage import CallRecord

MAX_OUTPUT_TOKENS = 1024
RUN_PATH = PROJECT_ROOT / "docs" / "day4-run.jsonl"
SCORES_PATH = PROJECT_ROOT / "docs" / "day4-scores.jsonl"


def _load_jsonl(filename: str) -> list[dict[str, Any]]:
    path = PROJECT_ROOT / "cases" / filename
    return [
        dict(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class RecordingAdapter:
    """Convert adapter attempts into the repository's Day 4 usage contract."""

    provider: str
    model_id: str

    def __init__(self, inner: OllamaAdapter, model_name: str) -> None:
        self._inner = inner
        self.provider = inner.provider
        self.model_id = inner.model_id
        self.model_name = model_name
        self.records: list[UsageRecord] = []
        self._case_record_start = 0
        self.complete_calls = 0

    def start_case(self) -> None:
        self._case_record_start = len(self.records)
        self.complete_calls = 0

    def _mark_previous_response_invalid(self) -> None:
        for index in range(len(self.records) - 1, self._case_record_start - 1, -1):
            if self.records[index].status == "success":
                self.records[index] = self.records[index].model_copy(
                    update={"status": "schema_invalid"}
                )
                return

    def mark_final_response_invalid(self) -> None:
        self._mark_previous_response_invalid()

    def complete(self, request: CompletionRequest, run_id: str) -> CompletionResult:
        if self.complete_calls:
            self._mark_previous_response_invalid()
        self.complete_calls += 1

        result = self._inner.complete(request, run_id)
        for call in result.records:
            self.records.append(self._usage_record(call, repair=self.complete_calls > 1))
        return result

    def _usage_record(self, call: CallRecord, *, repair: bool) -> UsageRecord:
        kind: Literal["primary", "transport_retry", "repair", "repair_retry"]
        if repair:
            kind = "repair" if call.attempt == 1 else "repair_retry"
        else:
            kind = "primary" if call.attempt == 1 else "transport_retry"

        status: Literal["success", "schema_invalid", "transport_error"]
        if call.error_type is None:
            status = "success"
        elif call.error_type in {
            TransientProviderError.__name__,
            PermanentProviderError.__name__,
        }:
            status = "transport_error"
        else:
            status = "schema_invalid"

        return UsageRecord(
            run_id=call.run_id,
            task=call.task,
            case_id=call.case_id,
            model_name=self.model_name,
            model_id=call.model_id,
            prompt_version=call.prompt_version,
            attempt=call.attempt,
            kind=kind,
            status=status,
            prompt_tokens=call.input_tokens,
            completion_tokens=call.output_tokens,
            latency_ms=call.latency_ms,
            cost_usd=Decimal(str(call.cost_usd)),
            error=call.error_type,
        )


def _gold_by_case() -> dict[str, Mapping[str, Any]]:
    return {str(row["id"]): row for row in _load_jsonl("gold/triage.jsonl")}


def _run_version(
    *,
    adapter: RecordingAdapter,
    run_id: str,
    prompt_version: str,
    cases: list[dict[str, Any]],
    gold: Mapping[str, Mapping[str, Any]],
    max_repairs: int,
) -> tuple[list[Record], list[Record]]:
    template = load("triage", prompt_version)
    schema = TriageOutput if prompt_version == "v1" else TriageOutputWithAnalysis
    run_records: list[Record] = []
    score_records: list[Record] = []

    for case in cases:
        case_id = str(case["id"])
        source = str(case["source"])
        request = CompletionRequest(
            task="triage",
            case_id=case_id,
            prompt_id="triage",
            prompt_version=prompt_version,
            system=template.system,
            user_content=render_user(template, variables={}, untrusted=source),
            temperature=0.0,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )

        adapter.start_case()
        try:
            output = complete_structured(
                adapter,
                request,
                schema,
                run_id,
                max_repairs=max_repairs,
            )
            output_record = OutputRecord(
                run_id=run_id,
                task="triage",
                case_id=case_id,
                model_name=adapter.model_name,
                model_id=adapter.model_id,
                prompt_version=prompt_version,
                succeeded=True,
                repairs=max(0, adapter.complete_calls - 1),
                output=output.model_dump(mode="json"),
            )
            result = "ok"
        except (TypeError, ValueError) as exc:
            adapter.mark_final_response_invalid()
            output_record = OutputRecord(
                run_id=run_id,
                task="triage",
                case_id=case_id,
                model_name=adapter.model_name,
                model_id=adapter.model_id,
                prompt_version=prompt_version,
                succeeded=False,
                repairs=max(0, adapter.complete_calls - 1),
                output=None,
                error=f"{type(exc).__name__}: {exc}",
            )
            result = "failed"

        case_usage = adapter.records[adapter._case_record_start :]
        run_records.extend(case_usage)
        run_records.append(output_record)
        score_records.extend(score_triage(output_record, gold[case_id]))
        print(
            f"{prompt_version} {case_id}: {result}; "
            f"calls={adapter.complete_calls}; repairs={output_record.repairs}",
            flush=True,
        )

    return run_records, score_records


def _write_records(path: Path, records: list[Record]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(record.model_dump_json() + "\n" for record in records),
        encoding="utf-8",
    )


def main() -> None:
    settings = Settings.from_env()
    model = settings.models["qwen"]
    adapter = RecordingAdapter(
        OllamaAdapter(model_id=model.model_id, think=False),
        model_name=model.logical_name,
    )
    run_id = str(uuid.uuid4())
    cases = _load_jsonl("triage.jsonl")
    gold = _gold_by_case()

    if len(cases) != 12:
        raise ValueError(f"expected 12 triage cases, found {len(cases)}")
    if {str(case["id"]) for case in cases} != set(gold):
        raise ValueError("triage cases and gold labels do not have matching ids")

    run_records: list[Record] = []
    score_records: list[Record] = []
    for prompt_version in ("v1", "v2"):
        version_runs, version_scores = _run_version(
            adapter=adapter,
            run_id=run_id,
            prompt_version=prompt_version,
            cases=cases,
            gold=gold,
            max_repairs=settings.max_schema_repairs,
        )
        run_records.extend(version_runs)
        score_records.extend(version_scores)

    _write_records(RUN_PATH, run_records)
    _write_records(SCORES_PATH, score_records)
    print(f"run_id={run_id}", flush=True)
    print(f"wrote {len(run_records)} records to {RUN_PATH}", flush=True)
    print(f"wrote {len(score_records)} records to {SCORES_PATH}", flush=True)


if __name__ == "__main__":
    main()
