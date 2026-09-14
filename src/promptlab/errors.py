"""Errors used by the Week 2 local model lab."""


class UnknownModelError(ValueError):
    """Raised when a model identifier is not present in the configured model table."""


class TransientProviderError(RuntimeError):
    """Timeout, connection failure, or temporary Ollama/server failure. Retried."""

    def __init__(self, message: str, *, latency_ms: int = 0) -> None:
        super().__init__(message)
        self.latency_ms = latency_ms


class PermanentProviderError(RuntimeError):
    """Malformed request, unavailable model, or other non-retryable provider failure."""

    def __init__(self, message: str, *, latency_ms: int = 0) -> None:
        super().__init__(message)
        self.latency_ms = latency_ms


class TruncatedResponseError(RuntimeError):
    """Ollama reported that the output token ceiling was reached. Not retried."""

    def __init__(
        self,
        message: str = "output token ceiling reached",
        *,
        text: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: int = 0,
        stop_reason: str = "length",
    ) -> None:
        super().__init__(message)
        self.text = text
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.latency_ms = latency_ms
        self.stop_reason = stop_reason
