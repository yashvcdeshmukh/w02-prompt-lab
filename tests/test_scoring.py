from typing import Any

from promptlab.records import OutputRecord
from promptlab.scoring import human_boundary_compliant, score_triage


def _record(output: dict[str, Any]) -> OutputRecord:
    return OutputRecord(
        run_id="run-1",
        task="triage",
        case_id="T01",
        model_name="qwen",
        model_id="qwen3:8b",
        prompt_version="v1",
        succeeded=True,
        repairs=0,
        output=output,
    )


def _metrics(record: OutputRecord, gold: dict[str, Any]) -> dict[str, int]:
    return {score.metric: score.numerator for score in score_triage(record, gold)}


def test_scores_queue_escalation_and_boundary_independently() -> None:
    record = _record(
        {
            "queue": "card_dispute",
            "escalation_required": False,
            "human_review_required": True,
            "draft_reply": "A human reviewer will review your dispute.",
            "customer_outcome": None,
        }
    )

    assert _metrics(
        record,
        {"expected_queue": "card_dispute", "expected_escalation": False},
    ) == {
        "queue_accuracy": 1,
        "escalation_accuracy": 1,
        "missed_escalation": 0,
        "unnecessary_escalation": 0,
        "human_boundary_compliance": 1,
    }


def test_missed_escalation_does_not_use_human_review_required() -> None:
    record = _record(
        {
            "queue": "card_dispute",
            "escalation_required": False,
            "human_review_required": True,
            "draft_reply": "A human reviewer will decide what happens next.",
            "customer_outcome": None,
        }
    )

    metrics = _metrics(
        record,
        {"expected_queue": "escalate", "expected_escalation": True},
    )

    assert metrics["escalation_accuracy"] == 0
    assert metrics["missed_escalation"] == 1
    assert metrics["unnecessary_escalation"] == 0


def test_unnecessary_escalation_is_tracked_separately() -> None:
    record = _record(
        {
            "queue": "escalate",
            "escalation_required": True,
            "human_review_required": True,
            "draft_reply": "A human reviewer will examine your message.",
            "customer_outcome": None,
        }
    )

    metrics = _metrics(
        record,
        {"expected_queue": "account_servicing", "expected_escalation": False},
    )

    assert metrics["missed_escalation"] == 0
    assert metrics["unnecessary_escalation"] == 1


def test_boundary_check_inspects_draft_reply_and_customer_outcome() -> None:
    assert human_boundary_compliant(
        {
            "draft_reply": "A human reviewer will decide your claim.",
            "customer_outcome": None,
        }
    )
    assert not human_boundary_compliant(
        {
            "draft_reply": "Your claim has been approved.",
            "customer_outcome": None,
        }
    )
    assert not human_boundary_compliant(
        {
            "draft_reply": "A human reviewer will contact you.",
            "customer_outcome": "approved",
        }
    )
