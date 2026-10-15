"""Real provider adapters. Imported lazily; SDKs are optional extras.

API keys are read from the environment at call time and never stored in config,
state, checkpoints or reports.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

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
        prompt = _json_prompt(request)
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


def _json_prompt(request: ModelRequest) -> str:
    return (
        f"{request.prompt}\n\nRespond with a single JSON object matching this JSON schema "
        f"(no prose outside the JSON):\n{json.dumps(request.json_schema)}"
    )


class OpenRouterAdapter:
    """Any OpenRouter model via its OpenAI-compatible HTTP API (stdlib only, no extra).

    Key: OPENROUTER_API_KEY. Endpoint: OPENROUTER_BASE_URL (default: the public API).
    OpenRouter reports usage and cost, so token and cost budgets are enforced.
    """

    name = "openrouter"
    DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, model: str, *, temperature: float = 0.0, max_output_tokens: int = 8192) -> None:
        if not os.environ.get("OPENROUTER_API_KEY"):
            raise ModelProviderError("OPENROUTER_API_KEY is not set")
        self.base_url = os.environ.get("OPENROUTER_BASE_URL", self.DEFAULT_BASE_URL).rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    def complete(self, request: ModelRequest, *, timeout: float) -> ModelResponse:
        body = {
            "model": self.model,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": _json_prompt(request)},
            ],
            "usage": {"include": True},
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers={
                "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
        except TimeoutError as exc:
            raise ModelTimeout(str(exc)) from exc
        except urllib.error.HTTPError as exc:
            raise ModelProviderError(f"HTTP {exc.code}: {exc.read()[:500].decode(errors='replace')}") from exc
        except (urllib.error.URLError, ValueError) as exc:
            if isinstance(getattr(exc, "reason", None), TimeoutError):
                raise ModelTimeout(str(exc)) from exc
            raise ModelProviderError(str(exc)) from exc
        if "error" in data or not data.get("choices"):
            raise ModelProviderError(f"provider error: {str(data.get('error', data))[:500]}")
        usage = data.get("usage") or {}
        return ModelResponse(
            text=data["choices"][0]["message"].get("content") or "",
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            cost_usd=usage.get("cost"),
            model=data.get("model", self.model),
        )


def make_adapter(provider: str, model: str, *, temperature: float, max_output_tokens: int):
    if provider == "anthropic":
        return AnthropicAdapter(model, temperature=temperature, max_output_tokens=max_output_tokens)
    if provider == "openrouter":
        return OpenRouterAdapter(model, temperature=temperature, max_output_tokens=max_output_tokens)
    raise ModelProviderError(f"unknown provider {provider!r}")
