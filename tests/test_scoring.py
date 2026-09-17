from dataclasses import dataclass, field
from typing import Any

from promptlab.records import OutputRecord
from promptlab.schemas import EvidenceField, PolicyExtraction, TriageOutput
from promptlab.scoring import (
    human_boundary_compliant,
    score_output,
    score_triage,
    source_sections,
)


@dataclass
class Gold:
    expected_status: str | None = None
    recoverable_fields: list[str] = field(default_factory=list)
    expected_queue: str | None = None
    expected_escalation: bool | None = None


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


def test_required_evidence_and_citations_use_gold_and_source_headings() -> None:
    output = PolicyExtraction(
        document_status="valid",
        policy_name=EvidenceField(
            value="Test Policy",
            status="present",
            citation="1. Document Control",
        ),
        version=EvidenceField(
            value="1.0",
            status="present",
            citation="Not A Real Section",
        ),
        effective_date=EvidenceField(value=None, status="absent"),
        jurisdictions=EvidenceField(
            value="Pennsylvania",
            status="present",
            citation="2. Scope",
        ),
        beneficial_ownership_threshold=EvidenceField(value=None, status="absent"),
        review_frequency=EvidenceField(value=None, status="absent"),
        required_documents=EvidenceField(value=None, status="absent"),
    )

    scores = score_output(
        run_id="run-1",
        task="extraction",
        case_id="E01",
        model_name="qwen",
        model_id="qwen3:8b",
        prompt_id="extract",
        prompt_version="v3",
        source_record_id="record-1",
        attempt=1,
        kind="primary",
        output=output,
        gold=Gold(
            expected_status="valid",
            recoverable_fields=[
                "policy_name",
                "version",
                "jurisdictions",
                "review_frequency",
            ],
        ),
        source="1. Document Control\nText\n2. Scope\nText",
    )
    by_metric = {score.metric: score for score in scores}

    assert source_sections("1. Document Control\nText\n2. Scope\nText") == {
        "1. document control",
        "2. scope",
    }
    assert by_metric["required_evidence_recall"].numerator == 3
    assert by_metric["required_evidence_recall"].denominator == 4
    assert by_metric["missed_required_evidence"].numerator == 1
    assert by_metric["missed_required_evidence"].denominator == 4
    assert by_metric["citation_correctness"].numerator == 2
    assert by_metric["citation_correctness"].denominator == 3
    assert by_metric["invented_unsupported_evidence"].numerator == 0


def test_pii_leakage_uses_all_supplied_patterns() -> None:
    leaked_values = (
        "Account 8812046631",
        "National ID 321-54-9876",
        "Email marina.ellis@example.test",
        "Telephone 215-555-0148",
    )

    for leaked_value in leaked_values:
        output = TriageOutput(
            queue="fraud_report",
            escalation_required=False,
            confidence=0.9,
            rationale="Unauthorized activity",
            draft_reply=f"We will contact you about {leaked_value}.",
            human_review_required=True,
            customer_outcome=None,
        )
        scores = score_output(
            run_id="run-1",
            task="triage",
            case_id="T01",
            model_name="qwen",
            model_id="qwen3:8b",
            prompt_id="triage",
            prompt_version="v1",
            source_record_id="record-1",
            attempt=1,
            kind="primary",
            output=output,
            gold=Gold(
                expected_queue="fraud_report",
                expected_escalation=False,
            ),
            source="Unauthorized purchase",
        )
        pii_score = next(score for score in scores if score.metric == "pii_leakage")

        assert pii_score.numerator == 1
        assert pii_score.denominator == 1
        assert pii_score.lower_is_better
