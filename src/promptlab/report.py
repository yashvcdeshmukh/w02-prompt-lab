"""Reporting for the Week 2 model-comparison lab.

The reporting layer consumes the existing UsageRecord, OutputRecord, and
ScoreRecord objects.  It does not rescore model output and it does not call an
LLM.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from fractions import Fraction
from pathlib import Path
from statistics import median
from typing import Any

from promptlab.records import OutputRecord, ScoreRecord, UsageRecord

_ConfigKey = tuple[str, str, str]  # task, model_name, prompt_version


def _key(record: Any) -> _ConfigKey:
    return (
        str(record.task),
        str(record.model_name),
        str(record.prompt_version),
    )


def _for_run(records: Sequence[Any], run_id: str) -> list[Any]:
    return [record for record in records if str(record.run_id) == run_id]


def _fmt_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.1f}"


def _aggregate_scores(
    records: Sequence[ScoreRecord],
) -> dict[str, tuple[int, int, bool | None]]:
    """Aggregate compatible score counts without averaging percentages."""

    grouped: dict[str, list[ScoreRecord]] = defaultdict(list)
    for record in records:
        grouped[str(record.metric)].append(record)

    result: dict[str, tuple[int, int, bool | None]] = {}

    for metric, rows in sorted(grouped.items()):
        numerator = sum(int(row.numerator) for row in rows)
        denominator = sum(int(row.denominator) for row in rows)

        directions = {
            bool(value)
            for value in (getattr(row, "lower_is_better", None) for row in rows)
            if value is not None
        }
        lower_is_better: bool | None
        lower_is_better = next(iter(directions)) if len(directions) == 1 else None

        result[metric] = (numerator, denominator, lower_is_better)

    return result


def _metric_text(records: Sequence[ScoreRecord]) -> str:
    metrics = _aggregate_scores(records)
    if not metrics:
        return "—"

    rendered: list[str] = []
    for metric, (numerator, denominator, lower_is_better) in metrics.items():
        suffix = " ↓" if lower_is_better else ""
        rendered.append(f"{metric}: {numerator}/{denominator}{suffix}")

    return "<br>".join(rendered)


def _usage_summary(
    records: Sequence[UsageRecord],
) -> tuple[str, str, str, str, str, str]:
    """Return token, latency, observation, and retry summaries."""

    if not records:
        return "—", "—", "—", "—", "0", "0"

    prompt_tokens_total = sum(int(getattr(row, "prompt_tokens", 0) or 0) for row in records)
    completion_tokens_total = sum(
        int(getattr(row, "completion_tokens", 0) or 0) for row in records
    )
    case_count = len({str(row.case_id) for row in records})
    prompt_tokens = _fmt_number(prompt_tokens_total / case_count)
    completion_tokens = _fmt_number(completion_tokens_total / case_count)

    latencies = [
        float(row.latency_ms)
        for row in records
        if getattr(row, "latency_ms", None) is not None
    ]

    if latencies:
        median_latency = f"{_fmt_number(float(median(latencies)))} ms"
        max_latency = f"{_fmt_number(float(max(latencies)))} ms"
    else:
        median_latency = "—"
        max_latency = "—"

    # A semantic repair is not also a transport retry.
    retry_attempts = sum(
        1
        for row in records
        if int(getattr(row, "attempt", 1) or 1) > 1
        and str(getattr(row, "kind", "")).lower() != "repair"
    )

    return (
        prompt_tokens,
        completion_tokens,
        median_latency,
        max_latency,
        str(len(latencies)),
        str(retry_attempts),
    )


def _output_summary(
    records: Sequence[OutputRecord],
) -> tuple[str, str, str, str, str, str]:
    if not records:
        return "0/0", "0/0", "0", "—", "—", "0"

    total = len(records)
    succeeded = sum(1 for row in records if bool(row.succeeded))
    repairs_needed = sum(
        1 for row in records if int(getattr(row, "repairs", 0) or 0) > 0
    )
    failures = total - succeeded
    case_latencies = [
        float(row.case_latency_ms)
        for row in records
        if row.case_latency_ms is not None
    ]
    if case_latencies:
        median_case_latency = f"{_fmt_number(float(median(case_latencies)))} ms"
        max_case_latency = f"{_fmt_number(float(max(case_latencies)))} ms"
    else:
        median_case_latency = "—"
        max_case_latency = "—"

    return (
        f"{succeeded}/{total}",
        f"{repairs_needed}/{total}",
        str(failures),
        median_case_latency,
        max_case_latency,
        str(len(case_latencies)),
    )


def _all_config_keys(
    usage: Sequence[UsageRecord],
    outputs: Sequence[OutputRecord],
    scores: Sequence[ScoreRecord],
) -> list[_ConfigKey]:
    keys = {_key(row) for row in usage}
    keys.update(_key(row) for row in outputs)
    keys.update(_key(row) for row in scores)
    return sorted(keys)


def _write_report(
    *,
    run_id: str,
    usage: Sequence[UsageRecord],
    outputs: Sequence[OutputRecord],
    scores: Sequence[ScoreRecord],
    report_path: Path,
) -> None:
    lines: list[str] = [
        "# Model Comparison",
        "",
        f"Run ID: `{run_id}`",
        "",
        "Counts are reported with their denominators. Case and model-call latency "
        "use median and maximum rather than mean.",
        "",
    ]

    keys = _all_config_keys(usage, outputs, scores)
    tasks = sorted({task for task, _model, _prompt in keys})

    if not tasks:
        lines.extend(
            [
                "No records were supplied for this run.",
                "",
            ]
        )

    for task in tasks:
        lines.extend(
            [
                f"## {task.title()}",
                "",
                "| Model | Prompt | Valid outputs | Metrics | Input tokens/case | "
                "Output tokens/case | Median case latency | Max case latency | Case n | "
                "Median call latency | Max call latency | Call n | "
                "Repairs | Retries | Final failures |",
                "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | "
                "---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )

        task_keys = [key for key in keys if key[0] == task]

        for key in task_keys:
            _task, model_name, prompt_version = key

            u = [row for row in usage if _key(row) == key]
            o = [row for row in outputs if _key(row) == key]
            s = [row for row in scores if _key(row) == key]

            (
                input_tokens,
                output_tokens,
                median_latency,
                max_latency,
                n,
                retries,
            ) = _usage_summary(u)

            (
                valid_outputs,
                repairs,
                failures,
                median_case_latency,
                max_case_latency,
                case_n,
            ) = _output_summary(o)
            metric_text = _metric_text(s)

            lines.append(
                "| "
                f"{model_name} | {prompt_version} | {valid_outputs} | "
                f"{metric_text} | {input_tokens} | {output_tokens} | "
                f"{median_case_latency} | {max_case_latency} | {case_n} | "
                f"{median_latency} | {max_latency} | {n} | {repairs} | "
                f"{retries} | {failures} |"
            )

        lines.append("")

    lines.extend(
        [
            "## Limits",
            "",
            "- Each task has only 12 cases. Results are directional, not production-scale "
            "estimates.",
            "- No prompt-transfer rows were run; every row identifies its model-qualified "
            "prompt version.",
            "- Untested combinations are Mistral with `-q` prompts and Qwen with `-m` "
            "prompts.",
            "- No production-volume reliability claim is being made.",
            "- Local Ollama latency depends on the lab hardware and concurrent workload.",
            "- A one-case difference such as 11/12 versus 10/12 is not a universal model "
            "ranking.",
            "- The triage human-boundary metric was tested under both Mistral and Qwen.",
            "- Local Ollama provider/API charge is `$0.00`; token usage and latency still "
            "represent real operational work.",
            "",
        ]
    )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")


def _ratio(
    metrics: dict[str, tuple[int, int, bool | None]],
    name: str,
) -> Fraction:
    numerator, denominator, _lower_is_better = metrics.get(name, (0, 0, None))
    return Fraction(numerator, denominator) if denominator else Fraction(0)


def _quality_rank(
    task: str,
    key: _ConfigKey,
    outputs: Sequence[OutputRecord],
    scores: Sequence[ScoreRecord],
) -> tuple[Fraction, ...]:
    config_outputs = [row for row in outputs if _key(row) == key]
    config_scores = [row for row in scores if _key(row) == key]
    metrics = _aggregate_scores(config_scores)
    valid = Fraction(
        sum(1 for row in config_outputs if row.succeeded),
        len(config_outputs) or 1,
    )

    if task == "triage":
        return (
            _ratio(metrics, "queue_accuracy"),
            _ratio(metrics, "escalation_accuracy"),
            _ratio(metrics, "human_boundary_compliance"),
            -_ratio(metrics, "pii_leakage"),
            valid,
        )
    if task == "summarization":
        return (
            _ratio(metrics, "document_status_accuracy"),
            _ratio(metrics, "citation_correctness"),
            _ratio(metrics, "required_evidence_recall"),
            -_ratio(metrics, "invented_unsupported_evidence"),
            valid,
        )
    return (
        valid,
        _ratio(metrics, "document_status_accuracy"),
        _ratio(metrics, "required_evidence_recall"),
        _ratio(metrics, "citation_correctness"),
        -_ratio(metrics, "invented_unsupported_evidence"),
    )


def _decision_evidence(
    task: str,
    key: _ConfigKey,
    outputs: Sequence[OutputRecord],
    scores: Sequence[ScoreRecord],
) -> str:
    config_outputs = [row for row in outputs if _key(row) == key]
    config_scores = [row for row in scores if _key(row) == key]
    metrics = _aggregate_scores(config_scores)
    valid = sum(1 for row in config_outputs if row.succeeded)
    total = len(config_outputs)

    names: tuple[str, ...]
    if task == "triage":
        names = (
            "queue_accuracy",
            "escalation_accuracy",
            "missed_escalation",
            "unnecessary_escalation",
            "human_boundary_compliance",
            "pii_leakage",
        )
    else:
        names = (
            "document_status_accuracy",
            "required_evidence_recall",
            "missed_required_evidence",
            "citation_correctness",
            "invented_unsupported_evidence",
        )

    parts = [f"valid outputs {valid}/{total}"]
    for name in names:
        if name in metrics:
            numerator, denominator, _lower_is_better = metrics[name]
            parts.append(f"{name} {numerator}/{denominator}")
    return "; ".join(parts)


def _write_decision_scaffold(
    *,
    run_id: str,
    models: Sequence[str],
    usage: Sequence[UsageRecord],
    outputs: Sequence[OutputRecord],
    scores: Sequence[ScoreRecord],
    decision_path: Path,
) -> None:
    """Write task-level recommendations from the measured records."""

    keys = _all_config_keys(usage, outputs, scores)

    lines: list[str] = [
        "# Model Decision Record",
        "",
        f"Run ID: `{run_id}`",
        "",
        "Decisions are task-specific and based on this run's measured configurations. "
        "They are not a universal model ranking.",
        "",
        "## Evaluated models",
        "",
    ]

    evaluated_models = sorted(
        {model for _task, model, _prompt in keys} | {str(model) for model in models}
    )
    if evaluated_models:
        for model in evaluated_models:
            lines.append(f"- {model}")
    else:
        lines.append("- None")

    lines.extend(["", "## Evaluated configurations", ""])

    if keys:
        for task, model, prompt in keys:
            lines.append(f"- `{task}` — {model} — `{prompt}`")
    else:
        lines.append("- No configurations supplied.")

    lines.extend(["", "## Task decisions", ""])
    tasks = sorted({task for task, _model, _prompt in keys})
    for task in tasks:
        task_keys = [key for key in keys if key[0] == task]
        selected = max(
            task_keys,
            key=lambda key: _quality_rank(task, key, outputs, scores),
        )
        _task, selected_model, selected_prompt = selected
        lines.extend(
            [
                f"### {task.title()}",
                "",
                "Evidence:",
            ]
        )
        for key in task_keys:
            _row_task, model, prompt = key
            evidence = _decision_evidence(task, key, outputs, scores)
            lines.append(f"- {model} — `{prompt}` — {evidence}")

        lines.extend(
            [
                "",
                f"Decision: **{selected_model}** with `{selected_prompt}`.",
                "",
                "Reason: "
                + _decision_evidence(task, selected, outputs, scores)
                + ".",
                "",
                "Rejected alternatives:",
            ]
        )
        rejected = [key for key in task_keys if key != selected]
        if rejected:
            for key in rejected:
                _row_task, model, prompt = key
                lines.append(
                    f"- {model} with `{prompt}`: "
                    + _decision_evidence(task, key, outputs, scores)
                    + "."
                )
        else:
            lines.append("- None measured.")

        lines.extend(
            [
                "",
                "Review trigger: Reopen after a larger representative evaluation, a "
                "material quality or human-boundary regression, a hardware/runtime "
                "change, or evidence that an adapted rejected configuration improves "
                "the task-level quality counts.",
                "",
            ]
        )

    decision_path.parent.mkdir(parents=True, exist_ok=True)
    decision_path.write_text("\n".join(lines), encoding="utf-8")


def write_reports(
    *,
    run_id: str,
    models: Sequence[str],
    usage: Sequence[UsageRecord],
    outputs: Sequence[OutputRecord],
    scores: Sequence[ScoreRecord],
    report_path: Path,
    decision_path: Path,
) -> None:
    """Generate the comparison report and decision scaffold for one run.

    Only records whose ``run_id`` matches the requested run are included.
    """

    run_usage = _for_run(usage, run_id)
    run_outputs = _for_run(outputs, run_id)
    run_scores = _for_run(scores, run_id)

    _write_report(
        run_id=run_id,
        usage=run_usage,
        outputs=run_outputs,
        scores=run_scores,
        report_path=Path(report_path),
    )

    _write_decision_scaffold(
        run_id=run_id,
        models=models,
        usage=run_usage,
        outputs=run_outputs,
        scores=run_scores,
        decision_path=Path(decision_path),
    )
