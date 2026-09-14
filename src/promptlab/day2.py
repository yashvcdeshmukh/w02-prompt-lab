"""Day 2: run the baseline prompt on Mistral and Qwen via OllamaAdapter."""

from __future__ import annotations

import json
import uuid
from typing import Any

from promptlab.adapters.base import CompletionRequest
from promptlab.adapters.ollama import OllamaAdapter
from promptlab.config import PROJECT_ROOT, Settings
from promptlab.usage import CallRecord, append_record

PROMPT_ID = "baseline"
PROMPT_VERSION = "v0"
MAX_OUTPUT_TOKENS = 1024
EVIDENCE_PATH = PROJECT_ROOT / "docs" / "day2-run.jsonl"


def load_summarization_cases() -> list[tuple[str, str]]:
    path = PROJECT_ROOT / "cases" / "summarization.jsonl"
    cases: list[tuple[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        payload: dict[str, Any] = json.loads(line)
        cases.append((str(payload["id"]), str(payload["source"])))
    return cases


def main() -> None:
    settings = Settings.from_env()
    template = (PROJECT_ROOT / "src" / "prompts" / "baseline.v0.md").read_text(
        encoding="utf-8"
    )
    system = template.split("<document>", 1)[0].strip()
    cases = load_summarization_cases()
    run_id = str(uuid.uuid4())
    records: list[CallRecord] = []

    for model in settings.models.values():
        adapter = OllamaAdapter(model_id=model.model_id)
        for case_id, source in cases:
            result = adapter.complete(
                CompletionRequest(
                    task="summarization",
                    case_id=case_id,
                    prompt_id=PROMPT_ID,
                    prompt_version=PROMPT_VERSION,
                    system=system,
                    user_content=source,
                    temperature=settings.temperature,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                ),
                run_id,
            )
            for record in result.records:
                append_record(record, run_id)
                records.append(record)

    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(
        "".join(record.model_dump_json() + "\n" for record in records),
        encoding="utf-8",
    )
    print(f"run_id={run_id}")
    print(f"wrote {len(records)} records to {EVIDENCE_PATH}")


if __name__ == "__main__":
    main()
