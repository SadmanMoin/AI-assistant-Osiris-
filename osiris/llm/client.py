"""Configurable LM Studio client with timeouts and a retry limit.

The planner uses this client for language only. Tool execution is separate
and does not run inside the model request.
"""

from __future__ import annotations

from osiris.audit.redact import redact_text
from osiris.errors import ExecutionError

_RETRYABLE_NAMES = {
    "APIConnectionError",
    "APITimeoutError",
    "ConnectError",
    "ReadTimeout",
    "ConnectTimeout",
    "ConnectionError",
    "TimeoutError",
}


def _retryable(error: Exception) -> bool:
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    return type(error).__name__ in _RETRYABLE_NAMES


class OpenAITransport:
    """Adapter over the installed OpenAI SDK, pointed at LM Studio."""

    def __init__(self, settings) -> None:
        from openai import OpenAI

        self.client = OpenAI(
            base_url=settings.lm_base_url,
            api_key=settings.api_key(),
            timeout=settings.timeout_seconds,
            max_retries=0,
        )

    def list_models(self, timeout: float | None = None) -> list[str]:
        data = self.client.models.list(timeout=timeout).data
        return [item.id for item in data]

    def chat(self, model: str, messages: list[dict], temperature: float) -> str:
        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
        )
        content = response.choices[0].message.content
        return content or ""


class LMStudioClient:
    def __init__(self, settings, transport=None) -> None:
        self.settings = settings
        self.transport = transport if transport is not None else OpenAITransport(settings)

    def list_models(self) -> list[str]:
        return self._retry(self.transport.list_models)

    def complete_chat(self, messages: list[dict], temperature: float | None = None) -> str:
        model = self.resolve_model()
        chosen = self.settings.temperature if temperature is None else temperature
        return self._retry(lambda: self.transport.chat(model, messages, chosen))

    def complete_planner(self, messages: list[dict]) -> str:
        return self.complete_chat(messages, temperature=self.settings.planner_temperature)

    def resolve_model(self) -> str:
        chosen = (self.settings.lm_model or "").strip()
        if chosen:
            return chosen
        models = self.list_models()
        if not models:
            raise ExecutionError("LM Studio-তে কোনো মডেল পাওয়া যায়নি।")
        return models[0]

    def check_connection(self) -> dict:
        """One short attempt so the status light does not sit on Connecting."""
        try:
            models = self.transport.list_models(timeout=5)
        except Exception as error:
            return {
                "ok": False,
                "models": [],
                "error": redact_text(str(error), self.settings.secret_values()),
            }
        if not models:
            return {"ok": False, "models": [], "error": "LM Studio-তে কোনো মডেল পাওয়া যায়নি।"}
        return {"ok": True, "models": models, "error": ""}

    def _retry(self, fn):
        attempts = int(self.settings.max_retries) + 1
        last_error = None
        for index in range(attempts):
            try:
                return fn()
            except Exception as error:
                last_error = error
                if not _retryable(error) or index == attempts - 1:
                    raise
        raise last_error
