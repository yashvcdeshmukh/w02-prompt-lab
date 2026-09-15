from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

from promptlab.adapters.base import CompletionRequest, CompletionResult, ModelAdapter


def _parse_json(text: str | None) -> object:
    if text is None or not text.strip():
        raise ValueError("empty model output")
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").strip()
        if stripped.endswith("```"):
            stripped = stripped[: -3].strip()
    return json.loads(stripped)


def _repair_request(
    request: CompletionRequest,
    previous: CompletionResult,
    error: Exception,
) -> CompletionRequest:
    previous_text = previous.text if previous.text is not None else ""
    user_content = (
        f"{request.user_content}\n\n"
        "The previous JSON failed validation.\n"
        f"Validation error:\n{error}\n\n"
        f"Previous output:\n{previous_text}\n\n"
        "Correct only what the validation error concerns. "
        "Return a single JSON object that matches the schema."
    )
    return request.model_copy(update={"user_content": user_content})


def complete_structured[T: BaseModel](
    adapter: ModelAdapter,
    request: CompletionRequest,
    schema: type[T],
    run_id: str,
    max_repairs: int = 1,
) -> T:
    """Return a schema-validated completion with a bounded semantic repair loop.

    Transport retry remains inside the adapter.
    Schema/content repair belongs here.

    On validation failure, send the validation error text back to the model and
    instruct it to correct only what the error concerns. Do not perform more
    than max_repairs semantic repair attempts.
    """

    result = adapter.complete(request, run_id)
    repairs_used = 0
    while True:
        try:
            payload = _parse_json(result.text)
            return schema.model_validate(payload)
        except (ValidationError, json.JSONDecodeError, TypeError, ValueError) as exc:
            if repairs_used >= max_repairs:
                raise
            repairs_used += 1
            result = adapter.complete(_repair_request(request, result, exc), run_id)
