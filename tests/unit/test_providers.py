"""OpenRouter adapter: request shape, usage/cost mapping, typed errors. No network."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from magi_system.llm import providers
from magi_system.llm.base import ModelProviderError, ModelRequest, ModelTimeout

REQ = ModelRequest(
    agent="magi.melchior", purpose="analysis", round=1, system="sys", prompt="do it",
    schema_name="X", json_schema={"type": "object"}, prompt_version="v1",
)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    return providers.make_adapter("openrouter", "vendor/model", temperature=0.0, max_output_tokens=100)


def test_missing_key_is_provider_error(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ModelProviderError, match="OPENROUTER_API_KEY"):
        providers.make_adapter("openrouter", "m", temperature=0.0, max_output_tokens=10)


def test_complete_maps_text_usage_and_cost(adapter, monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"], seen["body"], seen["auth"] = req.full_url, json.loads(req.data), req.get_header("Authorization")
        return _Resp(json.dumps({
            "model": "vendor/model",
            "choices": [{"message": {"content": '{"ok": true}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0012},
        }).encode())

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    out = adapter.complete(REQ, timeout=5)
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["model"] == "vendor/model" and seen["body"]["messages"][0] == {"role": "system", "content": "sys"}
    assert '"type": "object"' in seen["body"]["messages"][1]["content"]
    assert seen["body"]["response_format"] == {
        "type": "json_schema", "json_schema": {"name": "X", "strict": True, "schema": {"type": "object"}}}
    assert seen["body"]["provider"] == {"require_parameters": True}
    assert (out.text, out.input_tokens, out.output_tokens, out.cost_usd) == ('{"ok": true}', 10, 5, 0.0012)


def test_base_url_from_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "http://proxy.invalid/v1/")
    a = providers.make_adapter("openrouter", "m", temperature=0.0, max_output_tokens=10)
    assert a.base_url == "http://proxy.invalid/v1"


def test_errors_are_typed(adapter, monkeypatch):
    def timeout(req, timeout):
        raise TimeoutError("slow")

    monkeypatch.setattr(providers.urllib.request, "urlopen", timeout)
    with pytest.raises(ModelTimeout):
        adapter.complete(REQ, timeout=1)

    def http_error(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 429, "rate", {}, io.BytesIO(b"too many"))

    monkeypatch.setattr(providers.urllib.request, "urlopen", http_error)
    with pytest.raises(ModelProviderError, match="429"):
        adapter.complete(REQ, timeout=1)

    def reset(req, timeout):
        raise ConnectionResetError(54, "Connection reset by peer")

    monkeypatch.setattr(providers.urllib.request, "urlopen", reset)
    with pytest.raises(ModelProviderError, match="connection error"):
        adapter.complete(REQ, timeout=1)

    monkeypatch.setattr(providers.urllib.request, "urlopen", lambda req, timeout: _Resp(b'{"error": {"message": "bad model"}}'))
    with pytest.raises(ModelProviderError, match="bad model"):
        adapter.complete(REQ, timeout=1)


def test_finish_reason_length_marks_truncated(adapter, monkeypatch):
    body = {"choices": [{"message": {"content": '{"a": '}, "finish_reason": "length"}], "usage": {}}
    monkeypatch.setattr(providers.urllib.request, "urlopen", lambda req, timeout: _Resp(json.dumps(body).encode()))
    assert adapter.complete(REQ, timeout=1).truncated is True


def test_routing_and_reasoning_are_passed_through(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    a = providers.make_adapter("openrouter", "m", temperature=0.0, max_output_tokens=10,
                               routing={"sort": "throughput", "require_parameters": False}, reasoning={"effort": "low"})
    seen = {}

    def fake_urlopen(req, timeout):
        seen.update(json.loads(req.data))
        return _Resp(b'{"choices": [{"message": {"content": "{}"}}], "usage": {}}')

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake_urlopen)
    a.complete(REQ, timeout=1)
    # require_parameters cannot be switched off: schema enforcement depends on it
    assert seen["provider"] == {"sort": "throughput", "require_parameters": True}
    assert seen["reasoning"] == {"effort": "low"}


def test_routing_rejected_for_anthropic():
    with pytest.raises(ModelProviderError, match="only by provider 'openrouter'"):
        providers.make_adapter("anthropic", "m", temperature=0.0, max_output_tokens=10, routing={"sort": "latency"})
