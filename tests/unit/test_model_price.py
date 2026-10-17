"""Run cost computed from a configured model price (OpenRouter adapter)."""

import io
import json

import pytest

from magi_system.llm import providers
from magi_system.llm.base import ModelRequest
from magi_system.llm.providers import OpenRouterAdapter, computed_cost, make_adapter
from magi_system.models.run import ModelPrice

PRICE = ModelPrice(input_per_mtok=0.036, output_per_mtok=0.6435, cached_input_per_mtok=0.0054)


def _request():
    return ModelRequest(agent="a", purpose="p", round=0, system="s", prompt="x", schema_name="S",
                        json_schema={"type": "object"}, prompt_version="v1")


def _fake_http(monkeypatch, usage):
    body = json.dumps({"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}], "usage": usage, "model": "m"})
    monkeypatch.setattr(providers.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(body.encode()))


def test_cost_from_price_with_cached_tokens():
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 100_000,
             "prompt_tokens_details": {"cached_tokens": 600_000}}
    expected = (400_000 * 0.036 + 600_000 * 0.0054 + 100_000 * 0.6435) / 1_000_000
    assert computed_cost(PRICE, usage) == pytest.approx(expected)


def test_cached_tokens_default_to_the_input_price():
    price = ModelPrice(input_per_mtok=1.0, output_per_mtok=2.0)
    usage = {"prompt_tokens": 1_000_000, "completion_tokens": 500_000, "prompt_tokens_details": {"cached_tokens": 900_000}}
    assert computed_cost(price, usage) == pytest.approx(2.0)


def test_cached_tokens_never_exceed_the_prompt():
    usage = {"prompt_tokens": 100, "completion_tokens": 0, "prompt_tokens_details": {"cached_tokens": 5_000}}
    assert computed_cost(PRICE, usage) == pytest.approx(100 * 0.0054 / 1_000_000)


def test_no_usage_means_no_cost():
    assert computed_cost(PRICE, {}) is None
    assert computed_cost(PRICE, {"prompt_tokens": 10}) is None


def test_adapter_uses_the_price_and_keeps_the_provider_figure(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    _fake_http(monkeypatch, {"prompt_tokens": 1_000_000, "completion_tokens": 100_000, "cost": 9.99})
    resp = OpenRouterAdapter("m", price=PRICE).complete(_request(), timeout=5)
    assert resp.cost_usd == pytest.approx(0.036 + 0.06435)
    assert resp.provider_cost_usd == 9.99


def test_without_a_price_the_provider_cost_is_used(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    _fake_http(monkeypatch, {"prompt_tokens": 10, "completion_tokens": 10, "cost": 0.5})
    resp = OpenRouterAdapter("m").complete(_request(), timeout=5)
    assert resp.cost_usd == 0.5 and resp.provider_cost_usd == 0.5


def test_price_is_only_for_openrouter():
    with pytest.raises(providers.ModelProviderError):
        make_adapter("anthropic", "m", temperature=0.0, max_output_tokens=10, price=PRICE)
