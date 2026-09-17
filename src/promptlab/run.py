"""Integrated, reproducible evaluation harness for all Week 2 tasks."""

from __future__ import annotations

import argparse
import re
import time
from collections import defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Literal, cast

from promptlab.adapters.base import CompletionRequest, CompletionResult
from promptlab.adapters.ollama import OllamaAdapter
from promptlab.config import PROJECT_ROOT, ModelConfig, Settings
from promptlab.corpus import Case, GoldLabel, load_cases, validate_corpus
from promptlab.errors import PermanentProviderError, TransientProviderError
from promptlab.prompts import load, render_user
from promptlab.records import OutputRecord, Record, ScoreRecord, UsageRecord, append_record
from promptlab.report import write_reports
from promptlab.rules import VersionCandidate, select_current_version
from promptlab.schemas import (
    OUTPUT_SCHEMAS,
    PolicyExtraction,
    StrictModel,
    SummarizationOutput,
    TaskName,
    schema_description,
)
from promptlab.scoring import SCORER_VERSION, score_output
from promptlab.structured import complete_structured
from promptlab.usage import CallRecord

RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
MAX_OUTPUT_TOKENS = 1024
DAY5_RUN_PATH = PROJECT_ROOT / "docs" / "day5-run.jsonl"
DAY5_SCORES_PATH = PROJECT_ROOT / "docs" / "day5-scores.jsonl"

PromptSelection = tuple[str, str]
PromptConfig = tuple[TaskName, str]
PROMPT_SELECTIONS: dict[PromptConfig, PromptSelection] = {
    ("triage", "mistral"): ("triage", "v1-m"),
    ("triage", "qwen"): ("triage", "v1-q"),
    ("summarization", "mistral"): ("summarize", "v1-m"),
    ("summarization", "qwen"): ("summarize", "v1-q"),
    ("extraction", "mistral"): ("extract", "v3-m"),
    ("extraction", "qwen"): ("extract", "v3-q"),
}

AttemptKind = Literal["primary", "transport_retry", "repair", "repair_retry"]
AttemptStatus = Literal["success", "schema_invalid", "transport_error"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the local Mistral/Qwen evaluation harness"
    )
    parser.add_argument("--run-id", help="Stable identifier for this run")
    parser.add_argument(
        "--task",
        choices=["triage", "summarization", "extraction"],
        help="Run only one task",
    )
    parser.add_argument(
        "--model",
        choices=["mistral", "qwen"],
        help="Run only one configured model",
    )
    parser.add_argument("--limit", type=int, help="Limit cases per task for a smoke run")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the corpus and configuration without calling a model",
    )
    return parser


class RecordingAdapter:
    """Record every attempt while preserving the existing ModelAdapter contract."""

    provider: str
    model_id: str

    def __init__(self, inner: OllamaAdapter, model: ModelConfig) -> None:
        self._inner = inner
        self.provider = inner.provider
        self.model_id = inner.model_id
        self.model = model
        self.records: list[UsageRecord] = []
        self._case_start = 0
        self.complete_calls = 0

    def start_case(self) -> None:
        self._case_start = len(self.records)
        self.complete_calls = 0

    def case_records(self) -> list[UsageRecord]:
        return self.records[self._case_start :]

    def _mark_previous_response_invalid(self) -> None:
        for index in range(len(self.records) - 1, self._case_start - 1, -1):
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
            self.records.append(self._to_usage(call, repair=self.complete_calls > 1))
        return result

    def _to_usage(self, call: CallRecord, *, repair: bool) -> UsageRecord:
        kind: AttemptKind
        if repair:
            kind = "repair" if call.attempt == 1 else "repair_retry"
        else:
            kind = "primary" if call.attempt == 1 else "transport_retry"

        status: AttemptStatus
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
            model_name=self.model.logical_name,
            model_id=call.model_id,
            prompt_version=call.prompt_version,
            attempt=call.attempt,
            kind=kind,
            status=status,
            prompt_tokens=call.input_tokens,
            completion_tokens=call.output_tokens,
            latency_ms=call.latency_ms,
            cost_usd=Decimal(str(call.cost_usd)),
            record_id=call.record_id,
            prompt_id=call.prompt_id,
            error=call.error_type,
        )


def _selected_tasks(task: str | None) -> list[TaskName]:
    if task is not None:
        return [cast(TaskName, task)]
    return ["triage", "summarization", "extraction"]


def _selected_models(model: str | None, settings: Settings) -> list[str]:
    if model is not None:
        return [model]
    return list(settings.models)


def _render_request(
    *,
    task: TaskName,
    model_name: str,
    case: Case,
    schema: type[StrictModel],
    temperature: float,
) -> CompletionRequest:
    prompt_id, prompt_version = PROMPT_SELECTIONS[(task, model_name)]
    template = load(prompt_id, prompt_version)
    description = schema_description(schema)
    rendered = render_user(
        template,
        variables={
            "schema": description,
            "schema_description": description,
        },
        untrusted=case.document_text,
    )
    if (
        "{schema}" not in template.user_template
        and "{schema_description}" not in template.user_template
    ):
        rendered += (
            "\n\nReturn only one JSON object that validates against this schema:\n"
            + description
        )

    return CompletionRequest(
        task=task,
        case_id=case.id,
        prompt_id=prompt_id,
        prompt_version=prompt_version,
        system=template.system,
        user_content=rendered,
        temperature=temperature,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )


def _run_case(
    *,
    adapter: RecordingAdapter,
    run_id: str,
    task: TaskName,
    case: Case,
    gold: GoldLabel,
    max_repairs: int,
    temperature: float,
) -> tuple[OutputRecord, list[UsageRecord], list[ScoreRecord], StrictModel | None]:
    started = time.perf_counter()
    schema = OUTPUT_SCHEMAS[task]
    request = _render_request(
        task=task,
        model_name=adapter.model.logical_name,
        case=case,
        schema=schema,
        temperature=temperature,
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
    except (TypeError, ValueError) as exc:
        adapter.mark_final_response_invalid()
        case_usage = adapter.case_records()
        source_record_id = case_usage[-1].record_id if case_usage else None
        output_record = OutputRecord(
            run_id=run_id,
            task=task,
            case_id=case.id,
            model_name=adapter.model.logical_name,
            model_id=adapter.model_id,
            prompt_version=request.prompt_version,
            succeeded=False,
            repairs=max(0, adapter.complete_calls - 1),
            output=None,
            prompt_id=request.prompt_id,
            source_record_id=source_record_id,
            case_latency_ms=(time.perf_counter() - started) * 1000,
            error=f"{type(exc).__name__}: {exc}",
        )
        return output_record, case_usage, [], None

    case_usage = adapter.case_records()
    if not case_usage:
        raise RuntimeError(f"missing final call record for {task}/{case.id}")
    final_call = case_usage[-1]
    if final_call.record_id is None:
        raise RuntimeError(f"missing final call id for {task}/{case.id}")
    scores = score_output(
        run_id=run_id,
        task=task,
        case_id=case.id,
        model_name=adapter.model.logical_name,
        model_id=adapter.model_id,
        prompt_id=request.prompt_id,
        prompt_version=request.prompt_version,
        source_record_id=final_call.record_id,
        attempt=final_call.attempt,
        kind=final_call.kind,
        output=output,
        gold=gold,
        source=case.document_text,
    )
    output_record = OutputRecord(
        run_id=run_id,
        task=task,
        case_id=case.id,
        model_name=adapter.model.logical_name,
        model_id=adapter.model_id,
        prompt_version=request.prompt_version,
        succeeded=True,
        repairs=max(0, adapter.complete_calls - 1),
        output=output.model_dump(mode="json"),
        prompt_id=request.prompt_id,
        source_record_id=final_call.record_id,
        case_latency_ms=(time.perf_counter() - started) * 1000,
    )
    return output_record, case_usage, scores, output


def _version_fields(output: StrictModel) -> tuple[str, str] | None:
    if not isinstance(output, SummarizationOutput | PolicyExtraction):
        return None
    if (
        output.version.status != "present"
        or output.effective_date.status != "present"
        or not isinstance(output.version.value, str)
        or not isinstance(output.effective_date.value, str)
    ):
        return None
    return output.version.value, output.effective_date.value


def _version_scores(
    *,
    run_id: str,
    task: TaskName,
    model_name: str,
    model_id: str,
    prompt_id: str,
    prompt_version: str,
    labels: list[GoldLabel],
    outputs: dict[str, StrictModel],
    final_calls: dict[str, UsageRecord],
) -> list[ScoreRecord]:
    groups: dict[str, list[GoldLabel]] = defaultdict(list)
    for label in labels:
        if label.version_group is not None:
            groups[label.version_group].append(label)

    scores: list[ScoreRecord] = []
    for group_name, group_labels in sorted(groups.items()):
        expected_values = {
            label.expected_current_case_id
            for label in group_labels
            if label.expected_current_case_id is not None
        }
        as_of_values = {label.as_of for label in group_labels if label.as_of is not None}
        if len(expected_values) != 1 or len(as_of_values) != 1:
            raise ValueError(f"inconsistent version gold metadata for {group_name!r}")

        candidates: list[VersionCandidate] = []
        for label in group_labels:
            extracted = _version_fields(outputs[label.id]) if label.id in outputs else None
            if extracted is None:
                continue
            version, effective_raw = extracted
            try:
                effective_date = date.fromisoformat(effective_raw)
            except ValueError:
                continue
            candidates.append(
                VersionCandidate(
                    case_id=label.id,
                    version=version,
                    effective_date=effective_date,
                )
            )

        expected = next(iter(expected_values))
        source_call = final_calls.get(expected)
        if source_call is None or source_call.record_id is None:
            raise ValueError(
                f"missing call provenance for version score {group_name!r}/{expected}"
            )
        selected = select_current_version(
            candidates,
            date.fromisoformat(next(iter(as_of_values))),
        )
        selected_id = selected.case_id if selected is not None else None
        scores.append(
            ScoreRecord(
                run_id=run_id,
                task=task,
                case_id=expected,
                model_name=model_name,
                prompt_version=prompt_version,
                scorer_version=SCORER_VERSION,
                metric="version_selection_accuracy",
                numerator=int(selected_id == expected),
                denominator=1,
                model_id=model_id,
                prompt_id=prompt_id,
                source_record_id=source_call.record_id,
                attempt=source_call.attempt,
                kind=source_call.kind,
                detail=(
                    f"group={group_name}; expected={expected}; selected={selected_id}"
                ),
            )
        )
    return scores


def _append_all(path: Path, records: Sequence[Record]) -> None:
    for record in records:
        append_record(path, record)


def main() -> None:
    args = _parser().parse_args()
    counts = validate_corpus()
    settings = Settings.from_env()

    if args.validate_only:
        print("Corpus valid: " + ", ".join(f"{task}={count}" for task, count in counts.items()))
        return

    run_id = cast(str | None, args.run_id)
    if run_id is None or not RUN_ID_PATTERN.fullmatch(run_id):
        raise SystemExit("--run-id is required and must use letters, numbers, '.', '_' or '-'")

    limit = cast(int | None, args.limit)
    if limit is not None and limit < 1:
        raise SystemExit("--limit must be at least 1")

    selected_tasks = _selected_tasks(cast(str | None, args.task))
    selected_models = _selected_models(cast(str | None, args.model), settings)
    run_dir = PROJECT_ROOT / "runs" / run_id
    if run_dir.exists():
        raise SystemExit(f"Run directory already exists: {run_dir}")
    run_dir.mkdir(parents=True)

    usage_path = run_dir / "usage.jsonl"
    outputs_path = run_dir / "outputs.jsonl"
    scores_path = run_dir / "scores.jsonl"
    DAY5_RUN_PATH.parent.mkdir(parents=True, exist_ok=True)
    DAY5_RUN_PATH.write_text("", encoding="utf-8")
    DAY5_SCORES_PATH.write_text("", encoding="utf-8")
    all_usage: list[UsageRecord] = []
    all_outputs: list[OutputRecord] = []
    all_scores: list[ScoreRecord] = []
    total_cost = Decimal("0")
    for task in selected_tasks:
        pairs = load_cases(task)
        if limit is not None:
            pairs = pairs[:limit]

        for model_name in selected_models:
            model = settings.models[model_name]
            inner = OllamaAdapter(
                model_id=model.model_id,
                think=model.think,
            )
            adapter = RecordingAdapter(inner, model)
            validated: dict[str, StrictModel] = {}
            final_calls: dict[str, UsageRecord] = {}

            for case, gold in pairs:
                if total_cost >= settings.per_run_cap_usd:
                    raise SystemExit(
                        f"Per-run cost cap reached before {task}/{model_name}/{case.id}"
                    )

                output_record, usage, scores, output = _run_case(
                    adapter=adapter,
                    run_id=run_id,
                    task=task,
                    case=case,
                    gold=gold,
                    max_repairs=settings.max_schema_repairs,
                    temperature=settings.temperature,
                )
                _append_all(usage_path, usage)
                append_record(outputs_path, output_record)
                _append_all(scores_path, scores)
                day5_records: list[Record] = [*usage, output_record]
                _append_all(DAY5_RUN_PATH, day5_records)
                _append_all(DAY5_SCORES_PATH, scores)
                all_usage.extend(usage)
                all_outputs.append(output_record)
                all_scores.extend(scores)
                total_cost += sum((record.cost_usd for record in usage), Decimal("0"))
                if usage:
                    final_calls[case.id] = usage[-1]
                if output is not None:
                    validated[case.id] = output

                print(
                    f"{task:13} {model_name:8} {case.id:5} "
                    f"{'ok' if output_record.succeeded else 'failed'} "
                    f"repairs={output_record.repairs}",
                    flush=True,
                )

            version_scores = _version_scores(
                run_id=run_id,
                task=task,
                model_name=model_name,
                model_id=model.model_id,
                prompt_id=PROMPT_SELECTIONS[(task, model_name)][0],
                prompt_version=PROMPT_SELECTIONS[(task, model_name)][1],
                labels=[gold for _case, gold in pairs],
                outputs=validated,
                final_calls=final_calls,
            )
            _append_all(scores_path, version_scores)
            _append_all(DAY5_SCORES_PATH, version_scores)
            all_scores.extend(version_scores)

    write_reports(
        run_id=run_id,
        models=selected_models,
        usage=all_usage,
        outputs=all_outputs,
        scores=all_scores,
        report_path=PROJECT_ROOT / "reports" / "comparison.md",
        decision_path=PROJECT_ROOT / "docs" / "model-decision.md",
    )
    print(f"Run records: {run_dir}", flush=True)
    print(f"Day 5 run evidence: {DAY5_RUN_PATH}", flush=True)
    print(f"Day 5 score evidence: {DAY5_SCORES_PATH}", flush=True)
    print(f"Report: {PROJECT_ROOT / 'reports' / 'comparison.md'}", flush=True)
    print(f"Recorded provider/API cost: ${total_cost}", flush=True)


if __name__ == "__main__":
    main()
