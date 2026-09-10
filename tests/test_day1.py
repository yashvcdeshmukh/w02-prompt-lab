from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from promptlab.config import Settings
from promptlab.day1 import (
    CASE_IDS,
    MAX_OUTPUT_TOKENS,
    TRUNCATION_NUM_PREDICT,
    generate,
    load_baseline_prompt,
    load_extraction_sources,
    main,
    record_from_payload,
)


class FakeOllama:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        url: str,
        json: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        assert json is not None
        self.calls.append({"url": url, "body": json, "timeout": timeout})
        num_predict = int(json["options"]["num_predict"])
        if num_predict == TRUNCATION_NUM_PREDICT:
            payload: dict[str, Any] = {
                "response": "Policy",
                "prompt_eval_count": 50,
                "eval_count": 8,
                "done_reason": "length",
            }
        else:
            payload = {
                "response": "extracted policy fields",
                "prompt_eval_count": 100,
                "eval_count": 20,
                "done_reason": "stop",
            }
        return httpx.Response(200, request=httpx.Request("POST", url), json=payload)


def test_load_extraction_sources_includes_day1_cases() -> None:
    sources = load_extraction_sources()
    for case_id in CASE_IDS:
        assert case_id in sources
        assert sources[case_id].strip()
    assert len(sources["E12"]) < len(sources["E07"]) < len(sources["E11"])


def test_baseline_prompt_replaces_document_placeholder() -> None:
    template = load_baseline_prompt()
    sources = load_extraction_sources()
    filled = template.replace("{document_text}", sources["E12"])
    assert "{document_text}" in template
    assert "{document_text}" not in filled
    assert sources["E12"] in filled


def test_record_from_payload_maps_ollama_fields() -> None:
    model_id = Settings.from_env().models["mistral"].model_id
    record = record_from_payload(
        run_id="run-1",
        model_id=model_id,
        case_id="E12",
        temperature=0.0,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        payload={
            "response": "extracted",
            "prompt_eval_count": 40,
            "eval_count": 12,
            "done_reason": "stop",
        },
        latency_ms=123,
        error_type=None,
    )
    assert record.provider == "ollama"
    assert record.model_id == model_id
    assert record.task == "extraction"
    assert record.prompt_id == "baseline"
    assert record.prompt_version == "v0"
    assert record.input_tokens == 40
    assert record.output_tokens == 12
    assert record.stop_reason == "stop"
    assert record.response_text == "extracted"
    assert record.cached_input_tokens is None
    assert record.cost_usd == 0.0
    assert record.error_type is None
    assert record.timestamp.tzinfo is not None
    assert record.timestamp.utcoffset() is not None


def test_generate_posts_to_ollama_generate(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeOllama()
    monkeypatch.setattr("promptlab.day1.httpx.post", fake)
    payload, latency_ms = generate(
        base_url="http://host.docker.internal:11434",
        model_id="configured-model",
        prompt="hello",
        temperature=0.0,
        num_predict=MAX_OUTPUT_TOKENS,
    )
    assert fake.calls[0]["url"].endswith("/api/generate")
    assert fake.calls[0]["body"]["model"] == "configured-model"
    assert fake.calls[0]["body"]["options"]["temperature"] == 0.0
    assert payload["done_reason"] == "stop"
    assert latency_ms >= 0


def test_main_records_truncation_and_three_successes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    evidence_path = tmp_path / "docs" / "day1-run.jsonl"
    monkeypatch.setattr("promptlab.day1.EVIDENCE_PATH", evidence_path)
    fake = FakeOllama()
    monkeypatch.setattr("promptlab.day1.httpx.post", fake)

    main()

    model_id = Settings.from_env().models["mistral"].model_id
    sources = load_extraction_sources()
    assert len(fake.calls) == 4
    assert fake.calls[0]["body"]["options"]["num_predict"] == TRUNCATION_NUM_PREDICT
    assert sources["E11"] in fake.calls[0]["body"]["prompt"]
    for call in fake.calls:
        assert call["body"]["model"] == model_id
        assert call["body"]["options"]["temperature"] == 0.0
        assert "{document_text}" not in call["body"]["prompt"]
    for index, case_id in enumerate(CASE_IDS):
        success_call = fake.calls[index + 1]
        assert success_call["body"]["options"]["num_predict"] == MAX_OUTPUT_TOKENS
        assert sources[case_id] in success_call["body"]["prompt"]

    evidence_lines = evidence_path.read_text(encoding="utf-8").splitlines()
    assert len(evidence_lines) == 3
    evidence = [json.loads(line) for line in evidence_lines]
    assert [row["case_id"] for row in evidence] == list(CASE_IDS)
    assert all(row["error_type"] is None for row in evidence)
    assert all(row["stop_reason"] == "stop" for row in evidence)
    assert all(row["model_id"] == model_id for row in evidence)
    assert all(row["input_tokens"] == 100 for row in evidence)
    assert all(row["output_tokens"] == 20 for row in evidence)

    run_files = list((tmp_path / "runs").glob("*.jsonl"))
    assert len(run_files) == 1
    run_lines = run_files[0].read_text(encoding="utf-8").splitlines()
    assert len(run_lines) == 4
    truncated = json.loads(run_lines[0])
    assert truncated["case_id"] == "E11"
    assert truncated["stop_reason"] == "length"
    assert truncated["error_type"] == "TruncatedResponseError"
    assert truncated["max_output_tokens"] == TRUNCATION_NUM_PREDICT
