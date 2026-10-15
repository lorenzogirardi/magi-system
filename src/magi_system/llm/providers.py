"""Real provider adapters. Imported lazily; SDKs are optional extras.

API keys are read from the environment at call time and never stored in config,
state, checkpoints or reports.
"""

from __future__ import annotations

import json
import os

from magi_system.llm.base import ModelProviderError, ModelRequest, ModelResponse, ModelTimeout


class AnthropicAdapter:
    """Claude via the `anthropic` SDK (`uv sync --extra anthropic`). Key: ANTHROPIC_API_KEY."""

    name = "anthropic"

    def __init__(self, model: str, *, temperature: float = 0.0, max_output_tokens: int = 8192) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise ModelProviderError("install the 'anthropic' extra to use provider 'anthropic'") from exc
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ModelProviderError("ANTHROPIC_API_KEY is not set")
        self._anthropic = anthropic
        self._client = anthropic.Anthropic()
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    def complete(self, request: ModelRequest, *, timeout: float) -> ModelResponse:  # pragma: no cover - network
        prompt = (
            f"{request.prompt}\n\nRespond with a single JSON object matching this JSON schema "
            f"(no prose outside the JSON):\n{json.dumps(request.json_schema)}"
        )
        try:
            msg = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_output_tokens,
                temperature=self.temperature,
                system=request.system,
                messages=[{"role": "user", "content": prompt}],
                timeout=timeout,
            )
        except self._anthropic.APITimeoutError as exc:
            raise ModelTimeout(str(exc)) from exc
        except self._anthropic.APIError as exc:
            raise ModelProviderError(str(exc)) from exc
        text = "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")
        return ModelResponse(
            text=text,
            input_tokens=msg.usage.input_tokens,
            output_tokens=msg.usage.output_tokens,
            cost_usd=None,  # not reported by the API; budgets fall back to token/call limits
            model=msg.model,
        )


def make_adapter(provider: str, model: str, *, temperature: float, max_output_tokens: int):
    if provider == "anthropic":
        return AnthropicAdapter(model, temperature=temperature, max_output_tokens=max_output_tokens)
    raise ModelProviderError(f"unknown provider {provider!r}")
