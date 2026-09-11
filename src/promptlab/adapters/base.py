from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel

from promptlab.usage import CallRecord

Task = Literal["triage", "summarization", "extraction"]


class CompletionRequest(BaseModel):
    """One completion the caller wants, including the case metadata needed to record it."""

    task: Task
    case_id: str
    prompt_id: str
    prompt_version: str
    system: str
    user_content: str
    temperature: float
    max_output_tokens: int


class CompletionResult(BaseModel):
    """Outcome of complete(): final text plus one CallRecord per attempt (including retries)."""

    succeeded: bool
    text: str | None
    error_type: str | None
    records: list[CallRecord]


class ModelAdapter(Protocol):
    """Bound to one configured provider/model. complete() retries transport errors."""

    provider: str
    model_id: str

    def complete(self, request: CompletionRequest, run_id: str) -> CompletionResult:
        """Run the model. Return instrumented attempts; do not call httpx at the call site."""
        ...
