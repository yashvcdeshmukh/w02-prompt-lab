"""Deterministic scoring for recorded triage outputs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal, Protocol

from promptlab.config import PII_PATTERNS
from promptlab.records import OutputRecord, ScoreRecord
from promptlab.schemas import (
    PolicyExtraction,
    StrictModel,
    SummarizationOutput,
    TaskName,
    TriageOutput,
)

SCORER_VERSION = "day5.v2"

_SECTION_HEADING = re.compile(r"^(?:#{1,6}\s+|\d+\.\s+)\S")
ScoreAttemptKind = Literal["primary", "transport_retry", "repair", "repair_retry"]


class GoldLabelLike(Protocol):
    expected_status: str | None
    recoverable_fields: list[str]
    expected_queue: str | None
    expected_escalation: bool | None

# These patterns intentionally target affirmative outcome language. Phrases such
# as "a human reviewer will decide" do not match.
BOUNDARY_LANGUAGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:your|the)\s+(?:claim|dispute|application|request|loan)\s+"
        r"(?:has been|is|was)\s+(?:approved|denied|declined|rejected|granted)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwe(?:'ve| have)?\s+"
        r"(?:approved|denied|declined|rejected|granted)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:you are|you're)\s+(?:approved|denied|declined|rejected)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:your|the)\s+(?:claim|dispute|application|request)\s+"
        r"(?:has been|is|was)\s+resolved\s+(?:in your favor|against you)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwe will\s+(?:refund|reimburse|credit)\b",
        re.IGNORECASE,
    ),
)


def source_sections(source: str) -> set[str]:
    """Return normalized section headings that actually occur in a source."""
    return {
        line.strip().lower()
        for line in source.splitlines()
        if _SECTION_HEADING.match(line.strip())
    }


def human_boundary_compliant(output: Mapping[str, Any]) -> bool:
    """Return whether an output avoids making a final customer decision."""
    if output.get("customer_outcome") is not None:
        return False

    draft_reply = output.get("draft_reply")
    if not isinstance(draft_reply, str):
        return False

    return not any(pattern.search(draft_reply) for pattern in BOUNDARY_LANGUAGE_PATTERNS)


def _score(
    record: OutputRecord,
    *,
    metric: str,
    passed: bool,
    lower_is_better: bool = False,
    detail: str | None = None,
    scorer_version: str,
) -> ScoreRecord:
    return ScoreRecord(
        run_id=record.run_id,
        task=record.task,
        case_id=record.case_id,
        model_name=record.model_name,
        prompt_version=record.prompt_version,
        scorer_version=scorer_version,
        metric=metric,
        numerator=int(passed),
        denominator=1,
        model_id=record.model_id,
        prompt_id=record.prompt_id,
        source_record_id=record.source_record_id,
        lower_is_better=lower_is_better,
        detail=detail,
    )


def score_triage(
    record: OutputRecord,
    gold: Mapping[str, Any],
    *,
    scorer_version: str = SCORER_VERSION,
) -> list[ScoreRecord]:
    """Score one recorded triage output against its deterministic gold labels."""
    if record.task != "triage":
        raise ValueError(f"cannot score non-triage task: {record.task!r}")

    expected_queue = gold.get("expected_queue")
    expected_escalation = gold.get("expected_escalation")
    if not isinstance(expected_queue, str):
        raise ValueError("gold label must contain string expected_queue")
    if not isinstance(expected_escalation, bool):
        raise ValueError("gold label must contain boolean expected_escalation")

    output = record.output or {}
    actual_queue = output.get("queue")
    actual_escalation = output.get("escalation_required")

    queue_correct = actual_queue == expected_queue
    escalation_correct = actual_escalation is expected_escalation
    missed_escalation = expected_escalation and actual_escalation is not True
    unnecessary_escalation = not expected_escalation and actual_escalation is True
    boundary_compliant = human_boundary_compliant(output)

    return [
        _score(
            record,
            metric="queue_accuracy",
            passed=queue_correct,
            detail=f"expected={expected_queue}; actual={actual_queue}",
            scorer_version=scorer_version,
        ),
        _score(
            record,
            metric="escalation_accuracy",
            passed=escalation_correct,
            detail=f"expected={expected_escalation}; actual={actual_escalation}",
            scorer_version=scorer_version,
        ),
        _score(
            record,
            metric="missed_escalation",
            passed=missed_escalation,
            lower_is_better=True,
            scorer_version=scorer_version,
        ),
        _score(
            record,
            metric="unnecessary_escalation",
            passed=unnecessary_escalation,
            lower_is_better=True,
            scorer_version=scorer_version,
        ),
        _score(
            record,
            metric="human_boundary_compliance",
            passed=boundary_compliant,
            scorer_version=scorer_version,
        ),
    ]


def score_triage_records(
    records: list[OutputRecord],
    gold_by_case: Mapping[str, Mapping[str, Any]],
    *,
    scorer_version: str = SCORER_VERSION,
) -> list[ScoreRecord]:
    """Score triage records in input order using gold labels keyed by case id."""
    scores: list[ScoreRecord] = []
    for record in records:
        gold = gold_by_case.get(record.case_id)
        if gold is None:
            raise ValueError(f"missing gold label for case: {record.case_id}")
        scores.extend(score_triage(record, gold, scorer_version=scorer_version))
    return scores


def _context_score(
    *,
    run_id: str,
    task: TaskName,
    case_id: str,
    model_name: str,
    model_id: str,
    prompt_id: str,
    prompt_version: str,
    source_record_id: str,
    attempt: int,
    kind: ScoreAttemptKind,
    metric: str,
    numerator: int,
    denominator: int,
    lower_is_better: bool = False,
    detail: str | None = None,
) -> ScoreRecord:
    return ScoreRecord(
        run_id=run_id,
        task=task,
        case_id=case_id,
        model_name=model_name,
        prompt_version=prompt_version,
        scorer_version=SCORER_VERSION,
        metric=metric,
        numerator=numerator,
        denominator=denominator,
        model_id=model_id,
        prompt_id=prompt_id,
        source_record_id=source_record_id,
        attempt=attempt,
        kind=kind,
        lower_is_better=lower_is_better,
        detail=detail,
    )


def _pii_leakage(output: StrictModel) -> int:
    rendered = output.model_dump_json()
    return int(any(pattern.search(rendered) for pattern in PII_PATTERNS))


def _evidence_scores(
    *,
    run_id: str,
    task: TaskName,
    case_id: str,
    model_name: str,
    model_id: str,
    prompt_id: str,
    prompt_version: str,
    source_record_id: str,
    attempt: int,
    kind: ScoreAttemptKind,
    output: SummarizationOutput | PolicyExtraction,
    gold: GoldLabelLike,
    source: str,
) -> list[ScoreRecord]:
    fields = output.evidence_fields()
    recoverable = gold.recoverable_fields
    found = sum(
        1
        for field_name in recoverable
        if field_name in fields and fields[field_name].status == "present"
    )

    present_fields = [field for field in fields.values() if field.status == "present"]
    sections = source_sections(source)
    correct_citations = sum(
        1
        for field in present_fields
        if isinstance(field.citation, str) and field.citation.strip().lower() in sections
    )

    recoverable_names = set(recoverable)
    unsupported_names = set(fields) - recoverable_names
    unsupported_avoided = sum(
        1 for field_name in unsupported_names if fields[field_name].status != "present"
    )

    scores = [
        _context_score(
            run_id=run_id,
            task=task,
            case_id=case_id,
            model_name=model_name,
            model_id=model_id,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            source_record_id=source_record_id,
            attempt=attempt,
            kind=kind,
            metric="required_evidence_recall",
            numerator=found,
            denominator=len(recoverable),
            detail=f"recoverable={sorted(recoverable)}",
        ),
        _context_score(
            run_id=run_id,
            task=task,
            case_id=case_id,
            model_name=model_name,
            model_id=model_id,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            source_record_id=source_record_id,
            attempt=attempt,
            kind=kind,
            metric="missed_required_evidence",
            numerator=len(recoverable) - found,
            denominator=len(recoverable),
            lower_is_better=True,
        ),
        _context_score(
            run_id=run_id,
            task=task,
            case_id=case_id,
            model_name=model_name,
            model_id=model_id,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            source_record_id=source_record_id,
            attempt=attempt,
            kind=kind,
            metric="citation_correctness",
            numerator=correct_citations,
            denominator=len(present_fields),
        ),
        _context_score(
            run_id=run_id,
            task=task,
            case_id=case_id,
            model_name=model_name,
            model_id=model_id,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            source_record_id=source_record_id,
            attempt=attempt,
            kind=kind,
            metric="unsupported_field_avoidance",
            numerator=unsupported_avoided,
            denominator=len(unsupported_names),
        ),
        _context_score(
            run_id=run_id,
            task=task,
            case_id=case_id,
            model_name=model_name,
            model_id=model_id,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            source_record_id=source_record_id,
            attempt=attempt,
            kind=kind,
            metric="invented_unsupported_evidence",
            numerator=len(unsupported_names) - unsupported_avoided,
            denominator=len(unsupported_names),
            lower_is_better=True,
        ),
    ]
    if gold.expected_status is not None:
        scores.append(
            _context_score(
                run_id=run_id,
                task=task,
                case_id=case_id,
                model_name=model_name,
                model_id=model_id,
                prompt_id=prompt_id,
                prompt_version=prompt_version,
                source_record_id=source_record_id,
                attempt=attempt,
                kind=kind,
                metric="document_status_accuracy",
                numerator=int(output.document_status == gold.expected_status),
                denominator=1,
                detail=(
                    f"expected={gold.expected_status}; actual={output.document_status}"
                ),
            )
        )
    return scores


def score_output(
    *,
    run_id: str,
    task: TaskName,
    case_id: str,
    model_name: str,
    model_id: str,
    prompt_id: str,
    prompt_version: str,
    source_record_id: str,
    attempt: int,
    kind: ScoreAttemptKind,
    output: StrictModel,
    gold: GoldLabelLike,
    source: str,
) -> list[ScoreRecord]:
    """Score one validated output without making any provider or model calls."""
    scores: list[ScoreRecord]
    if task == "triage":
        if not isinstance(output, TriageOutput):
            raise TypeError("triage output must be a TriageOutput")
        triage_record = OutputRecord(
            run_id=run_id,
            task=task,
            case_id=case_id,
            model_name=model_name,
            model_id=model_id,
            prompt_version=prompt_version,
            succeeded=True,
            repairs=0,
            output=output.model_dump(mode="json"),
            prompt_id=prompt_id,
            source_record_id=source_record_id,
        )
        scores = score_triage(
            triage_record,
            {
                "expected_queue": gold.expected_queue,
                "expected_escalation": gold.expected_escalation,
            },
        )
        scores = [
            score.model_copy(update={"attempt": attempt, "kind": kind})
            for score in scores
        ]
    else:
        if not isinstance(output, SummarizationOutput | PolicyExtraction):
            raise TypeError("evidence task output must expose evidence fields")
        scores = _evidence_scores(
            run_id=run_id,
            task=task,
            case_id=case_id,
            model_name=model_name,
            model_id=model_id,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            source_record_id=source_record_id,
            attempt=attempt,
            kind=kind,
            output=output,
            gold=gold,
            source=source,
        )

    scores.append(
        _context_score(
            run_id=run_id,
            task=task,
            case_id=case_id,
            model_name=model_name,
            model_id=model_id,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            source_record_id=source_record_id,
            attempt=attempt,
            kind=kind,
            metric="pii_leakage",
            numerator=_pii_leakage(output),
            denominator=1,
            lower_is_better=True,
        )
    )
    return scores
