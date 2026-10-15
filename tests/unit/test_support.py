"""Redaction, structured output parsing, budget and prompt registry."""

import pytest
from pydantic import BaseModel

from magi_system import prompts
from magi_system.engine.budget import BudgetExhausted, BudgetTracker
from magi_system.llm import InvalidStructuredOutput, parse_structured
from magi_system.models.run import RunBudget
from magi_system.observability import REDACTED, EventLog, Redactor


@pytest.mark.parametrize(
    "text",
    [
        "https://bob:pw123456@host/x.git",
        "token=abcdef123456",
        "ghp_abcdefghijklmnopqrstuvwx1234",
        "glpat-abcdefghijklmnopqrstuvwx",
        "sk-ant-abcdefghijklmnopqrstuvwx",
        "AKIAABCDEFGHIJKLMNOP",
        "Authorization: Bearer abc.def.ghi",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_redaction(text):
    out = Redactor(include_env=False).text(f"before {text} after")
    assert REDACTED in out and "before" in out and "after" in out


def test_env_secrets_redacted(monkeypatch):
    monkeypatch.setenv("SERVICE_PASSWORD", "correct-horse-battery")
    assert "correct-horse" not in Redactor().text("pw is correct-horse-battery")


def test_event_log_redacts(tmp_path):
    log = EventLog(tmp_path / "e.jsonl", "r1", redactor=Redactor(["s3cr3t-value"], include_env=False))
    log.emit("x", detail="value s3cr3t-value here", nested={"k": ["s3cr3t-value"]})
    assert "s3cr3t-value" not in (tmp_path / "e.jsonl").read_text()
    assert EventLog.read(tmp_path / "e.jsonl")[0]["kind"] == "x"


class A(BaseModel):
    x: int


@pytest.mark.parametrize("text", ['{"x": 1}', "```json\n{\"x\": 1}\n```", 'prefix {"x": 1} suffix'])
def test_parse_structured(text):
    assert parse_structured(text, A).x == 1


@pytest.mark.parametrize("text", ["", "no json", '{"x": "a"}', "{broken", '{"x": 1}x'])
def test_parse_structured_rejects(text):
    with pytest.raises(InvalidStructuredOutput):
        parse_structured(text, A)


def test_budget_limits():
    t = BudgetTracker(RunBudget(max_llm_calls=2, max_tool_calls=1, max_tokens=10), started_at=__import__("time").time())
    t.before_llm_call()
    t.after_llm_call(8, 4, None)
    with pytest.raises(BudgetExhausted, match="tokens"):
        t.before_llm_call()
    t.on_tool_call()
    with pytest.raises(BudgetExhausted, match="tool_calls"):
        t.on_tool_call()
    assert t.snapshot()["cost_usd"] is None  # never invented when not reported


def test_budget_duration():
    t = BudgetTracker(RunBudget(max_duration_seconds=1), started_at=0)
    with pytest.raises(BudgetExhausted, match="duration"):
        t.check_time()


def test_prompts_are_versioned():
    man = prompts.manifest()
    assert set(man) == set(prompts.ALL_PROMPTS)
    assert all("@v" in ref and "#" in ref for ref in man.values())
    assert "{display_name}" not in prompts.load("magi_analysis").render(display_name="Melchior-Magi 1", perspective="p")
