"""Deterministic scoring for recorded triage outputs."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from promptlab.records import OutputRecord, ScoreRecord

SCORER_VERSION = "day4.v1"

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
