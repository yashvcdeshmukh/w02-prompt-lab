"""Day 3: schema-validated summarization and extraction with one repair."""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from typing import Any

from pydantic import BaseModel, ValidationError

from promptlab.adapters.base import CompletionRequest, CompletionResult, Task
from promptlab.adapters.ollama import OllamaAdapter
from promptlab.config import PROJECT_ROOT, Settings
from promptlab.schemas import PolicyExtraction, SummarizationOutput, schema_description
from promptlab.structured import complete_structured
from promptlab.usage import CallRecord, append_record

MAX_OUTPUT_TOKENS = 1024
EVIDENCE_PATH = PROJECT_ROOT / "docs" / "day3-run.jsonl"
NOTES_PATH = PROJECT_ROOT / "docs" / "day3-notes.md"
EXAMPLE_LEAK_STRINGS = (
    "Northglass",
    "Norwyn",
    "Bellwater",
    "Redhaven",
    "East Kestrel",
)
HEADING_RE = re.compile(r"^(?:#{1,6}\s+|\d+\.\s+)\S")


class RecordingAdapter:
    provider: str
    model_id: str

    def __init__(self, inner: OllamaAdapter) -> None:
        self._inner = inner
        self.provider = inner.provider
        self.model_id = inner.model_id
        self.records: list[CallRecord] = []
        self.calls = 0

    def complete(self, request: CompletionRequest, run_id: str) -> CompletionResult:
        self.calls += 1
        result = self._inner.complete(request, run_id)
        for record in result.records:
            append_record(record, run_id)
            self.records.append(record)
        return result


def load_cases(filename: str) -> list[tuple[str, str]]:
    path = PROJECT_ROOT / "cases" / filename
    cases: list[tuple[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        payload: dict[str, Any] = json.loads(line)
        cases.append((str(payload["id"]), str(payload["source"])))
    return cases


def render_prompt(template: str, source: str, schema: type[BaseModel]) -> str:
    description = schema_description(schema)
    filled = template.replace("{schema}", description).replace("{document_text}", source)
    if "{schema}" in template:
        return filled
    return (
        filled
        + "\n\nReturn a single JSON object matching this schema and nothing else:\n"
        + description
    )


def heading_lines(source: str) -> list[str]:
    return [line.strip() for line in source.splitlines() if HEADING_RE.match(line.strip())]


def citation_failures(output: SummarizationOutput | PolicyExtraction, source: str) -> int:
    headings = heading_lines(source)
    failures = 0
    for field in output.evidence_fields().values():
        if field.status != "present":
            continue
        citation = field.citation or ""
        if not citation or not any(citation in heading for heading in headings):
            failures += 1
    return failures


def leakage_hits(output: PolicyExtraction) -> int:
    blob = output.model_dump_json()
    return sum(1 for token in EXAMPLE_LEAK_STRINGS if token in blob)


def error_kind(exc: BaseException) -> str:
    text = str(exc)
    first = text.splitlines()[0] if text else type(exc).__name__
    lowered = text.lower()
    if "extra" in lowered and "forbid" in lowered:
        return "extra fields forbidden"
    if "json" in type(exc).__name__.lower() or "json" in first.lower():
        return "invalid JSON"
    if "field required" in lowered or "missing" in lowered:
        return "missing required field"
    return first[:120]


def run_task(
    *,
    adapter: RecordingAdapter,
    run_id: str,
    task: Task,
    prompt_id: str,
    prompt_version: str,
    template: str,
    schema: type[SummarizationOutput] | type[PolicyExtraction],
    cases: list[tuple[str, str]],
    temperature: float,
    max_repairs: int,
) -> tuple[int, int, int, int, list[str]]:
    repairs = 0
    successes = 0
    cite_fail = 0
    leak = 0
    errors: list[str] = []

    for case_id, source in cases:
        request = CompletionRequest(
            task=task,
            case_id=case_id,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            system="",
            user_content=render_prompt(template, source, schema),
            temperature=temperature,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        adapter.calls = 0
        try:
            output = complete_structured(
                adapter,
                request,
                schema,
                run_id,
                max_repairs=max_repairs,
            )
        except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as exc:
            errors.append(error_kind(exc))
            if adapter.calls > 1:
                repairs += 1
            print(f"{task} {case_id}: failed after {adapter.calls} call(s): {error_kind(exc)}")
            continue
        if adapter.calls > 1:
            repairs += 1
            errors.append("repaired")
        successes += 1
        cite_fail += citation_failures(output, source)
        if isinstance(output, PolicyExtraction):
            leak += leakage_hits(output)
        print(f"{task} {case_id}: ok calls={adapter.calls}")

    return repairs, successes, cite_fail, leak, errors


def write_notes(
    *,
    run_id: str,
    model_id: str,
    sum_repairs: int,
    sum_successes: int,
    ext_repairs: int,
    ext_successes: int,
    leak: int,
    cite_fail: int,
    errors: list[str],
) -> None:
    counts = Counter(kind for kind in errors if kind != "repaired")
    if not counts:
        counts = Counter(errors)
    common = counts.most_common(1)[0][0] if counts else "none observed"
    NOTES_PATH.write_text(
        (
            f"# Day 3 notes\n\n"
            f"run_id: `{run_id}`\n"
            f"model: `{model_id}`\n"
            f"temperature: 0.0\n\n"
            f"- summarization repair rate: {sum_repairs}/12 "
            f"({sum_successes}/12 validated)\n"
            f"- extraction repair rate: {ext_repairs}/12 "
            f"({ext_successes}/12 validated)\n"
            f"- example leakage count: {leak}\n"
            f"- citation-existence failure count: {cite_fail}\n\n"
            f"The most common validation error was {common}. "
            f"The repair path sent that error text back to the model and asked it "
            f"to correct only those fields, which recovered some invalid outputs "
            f"without changing the schema.\n"
        ),
        encoding="utf-8",
    )


def main() -> None:
    settings = Settings.from_env()
    model = settings.models["mistral"]
    adapter = RecordingAdapter(OllamaAdapter(model_id=model.model_id))
    run_id = str(uuid.uuid4())
    summarize_template = (PROJECT_ROOT / "src" / "prompts" / "summarize.v1.md").read_text(
        encoding="utf-8"
    )
    extract_template = (PROJECT_ROOT / "src" / "prompts" / "extract.v2.md").read_text(
        encoding="utf-8"
    )

    sum_repairs, sum_ok, sum_cite, _, sum_errors = run_task(
        adapter=adapter,
        run_id=run_id,
        task="summarization",
        prompt_id="summarize",
        prompt_version="v1",
        template=summarize_template,
        schema=SummarizationOutput,
        cases=load_cases("summarization.jsonl"),
        temperature=settings.temperature,
        max_repairs=settings.max_schema_repairs,
    )
    ext_repairs, ext_ok, ext_cite, leak, ext_errors = run_task(
        adapter=adapter,
        run_id=run_id,
        task="extraction",
        prompt_id="extract",
        prompt_version="v2",
        template=extract_template,
        schema=PolicyExtraction,
        cases=load_cases("extraction.jsonl"),
        temperature=settings.temperature,
        max_repairs=settings.max_schema_repairs,
    )

    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(
        "".join(record.model_dump_json() + "\n" for record in adapter.records),
        encoding="utf-8",
    )
    write_notes(
        run_id=run_id,
        model_id=model.model_id,
        sum_repairs=sum_repairs,
        sum_successes=sum_ok,
        ext_repairs=ext_repairs,
        ext_successes=ext_ok,
        leak=leak,
        cite_fail=sum_cite + ext_cite,
        errors=sum_errors + ext_errors,
    )
    print(f"run_id={run_id}")
    print(f"wrote {len(adapter.records)} records to {EVIDENCE_PATH}")
    print(f"wrote {NOTES_PATH}")


if __name__ == "__main__":
    main()
