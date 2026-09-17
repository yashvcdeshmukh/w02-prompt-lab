from datetime import UTC, datetime

from promptlab.adapters.base import CompletionRequest, CompletionResult
from promptlab.adapters.ollama import OllamaAdapter
from promptlab.config import ModelConfig, Settings
from promptlab.corpus import load_cases, validate_corpus
from promptlab.run import (
    PROMPT_SELECTIONS,
    RecordingAdapter,
    _render_request,
    _run_case,
    _selected_models,
    _selected_tasks,
)
from promptlab.schemas import SummarizationOutput
from promptlab.usage import CallRecord


class StubOllamaAdapter(OllamaAdapter):
    def __init__(self) -> None:
        self.model_id = "fixture-model"

    def complete(self, request: CompletionRequest, run_id: str) -> CompletionResult:
        text = (
            '{"queue":"card_dispute","escalation_required":false,'
            '"confidence":0.9,"rationale":"Duplicate recognized charge",'
            '"draft_reply":"A human will review your dispute.",'
            '"human_review_required":true,"customer_outcome":null}'
        )
        return CompletionResult(
            succeeded=True,
            text=text,
            error_type=None,
            records=[
                CallRecord(
                    record_id="record-1",
                    run_id=run_id,
                    timestamp=datetime.now(UTC),
                    provider="ollama",
                    model_id=self.model_id,
                    task=request.task,
                    case_id=request.case_id,
                    prompt_id=request.prompt_id,
                    prompt_version=request.prompt_version,
                    attempt=1,
                    temperature=request.temperature,
                    max_output_tokens=request.max_output_tokens,
                    input_tokens=100,
                    output_tokens=40,
                    cached_input_tokens=None,
                    latency_ms=25,
                    cost_usd=0.0,
                    stop_reason="stop",
                    error_type=None,
                    response_text=text,
                )
            ],
        )


def test_default_matrix_contains_72_evaluations() -> None:
    settings = Settings.from_env()
    tasks = _selected_tasks(None)
    models = _selected_models(None, settings)
    counts = validate_corpus()

    assert tasks == ["triage", "summarization", "extraction"]
    assert set(models) == {"mistral", "qwen"}
    assert sum(counts[task] * len(models) for task in tasks) == 72
    assert settings.models["mistral"].think is None
    assert settings.models["qwen"].think is True
    assert {selection[1] for selection in PROMPT_SELECTIONS.values()} == {
        "v1-m",
        "v1-q",
        "v3-m",
        "v3-q",
    }


def test_render_request_uses_registry_and_includes_schema() -> None:
    case, _gold = load_cases("summarization")[0]

    request = _render_request(
        task="summarization",
        model_name="mistral",
        case=case,
        schema=SummarizationOutput,
        temperature=0.0,
    )

    assert request.prompt_id == "summarize"
    assert request.prompt_version == "v1-m"
    assert case.document_text in request.user_content
    assert '"document_status"' in request.user_content


def test_run_case_uses_structured_path_and_records_attempt() -> None:
    case, gold = load_cases("triage")[0]
    model = ModelConfig(logical_name="qwen", model_id="fixture-model", think=True)
    adapter = RecordingAdapter(StubOllamaAdapter(), model)

    output, usage, scores, validated = _run_case(
        adapter=adapter,
        run_id="fixture-run",
        task="triage",
        case=case,
        gold=gold,
        max_repairs=1,
        temperature=0.0,
    )

    assert output.succeeded
    assert output.output is not None
    assert output.case_latency_ms is not None
    assert output.case_latency_ms > 0
    assert validated is not None
    assert len(usage) == 1
    assert usage[0].kind == "primary"
    queue_score = next(score for score in scores if score.metric == "queue_accuracy")
    assert queue_score.source_record_id == usage[0].record_id
    assert (
        queue_score.run_id,
        queue_score.case_id,
        queue_score.task,
        queue_score.model_id,
        queue_score.prompt_id,
        queue_score.prompt_version,
        queue_score.attempt,
        queue_score.kind,
    ) == (
        usage[0].run_id,
        usage[0].case_id,
        usage[0].task,
        usage[0].model_id,
        usage[0].prompt_id,
        usage[0].prompt_version,
        usage[0].attempt,
        usage[0].kind,
    )
