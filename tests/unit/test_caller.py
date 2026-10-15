"""AgentCaller.ask: invalid or truncated output is retried with the error fed back."""

from __future__ import annotations

import time

from pydantic import BaseModel

from magi_system.agents.caller import AgentCaller
from magi_system.engine.budget import BudgetTracker
from magi_system.llm.base import ModelRequest, ModelResponse, ScriptedModel
from magi_system.models.common import AgentId
from magi_system.models.decision import AgentFailure, FailureKind
from magi_system.models.run import RunBudget
from magi_system.observability.events import EventLog

AGENT = AgentId("magi.melchior")


class A(BaseModel):
    x: int


def _caller(model, tmp_path) -> AgentCaller:
    return AgentCaller(models={AGENT: model}, timeouts={AGENT: 5.0},
                       budget=BudgetTracker(RunBudget(), started_at=time.time()), events=EventLog(tmp_path / "e.jsonl", "r1"))


def _ask(caller):
    return caller.ask(AGENT, purpose="p", round=0, prompt_name="_common", prompt_values={}, body="BODY", schema=A, phase="t")


def test_retry_feeds_back_the_error_and_records_an_excerpt(tmp_path):
    model = ScriptedModel().set(AGENT.value, "p", ['{"x": 1}{"x": 2}', {"x": 3}])
    caller = _caller(model, tmp_path)
    assert _ask(caller).x == 3
    first, second = model.calls
    assert first.prompt == "BODY"
    assert second.prompt.startswith("BODY") and "previous response was rejected" in second.prompt and "Extra data" in second.prompt
    ev = [e for e in EventLog.read(tmp_path / "e.jsonl") if e["kind"] == "agent_invalid_output"][0]
    assert ev["excerpt"] == '{"x": 1}{"x": 2}'


class _Truncating:
    name = "trunc"

    def __init__(self):
        self.calls: list[ModelRequest] = []

    def complete(self, request, *, timeout):
        self.calls.append(request)
        return ModelResponse(text='{"x": "' + "ab" * 400, input_tokens=10, output_tokens=100, cost_usd=0.01, truncated=True)


def test_truncated_output_is_typed_counted_and_retried(tmp_path):
    model = _Truncating()
    caller = _caller(model, tmp_path)
    out = _ask(caller)
    assert isinstance(out, AgentFailure) and out.kind == FailureKind.INVALID_OUTPUT
    assert "truncated at max_output_tokens" in out.detail
    assert len(model.calls) == 2 and "truncated" in model.calls[1].prompt
    assert caller.budget.tokens == 220 and round(caller.budget.cost_usd, 2) == 0.02
    ex = [e for e in EventLog.read(tmp_path / "e.jsonl") if e["kind"] == "agent_invalid_output"][0]["excerpt"]
    assert ex.startswith('{"x": "abab') and "chars ...]" in ex and ex.endswith("abab")


def test_actions_beyond_the_per_turn_limit_are_answered_with_an_error(tmp_path):
    from magi_system.policy import PolicyEngine
    from magi_system.tools.toolbox import Toolbox
    (tmp_path / "ws").mkdir()
    (tmp_path / "ws/f.txt").write_text("hello")
    actions = [{"tool": "read_file", "args": {"path": "f.txt"}}] * 3
    model = ScriptedModel().set(AGENT.value, "p", [{"actions": actions}, {"final": {"x": 1}}])
    caller = _caller(model, tmp_path)
    caller.max_actions_per_turn = 2
    tb = Toolbox(role=AGENT, workspace=tmp_path / "ws", policy=PolicyEngine("offline-test"))
    out = caller.run_tools(AGENT, purpose="p", round=0, prompt_name="_common", prompt_values={}, body="B",
                           final_schema=A, toolbox=tb, phase="t")
    assert out.x == 1
    turn2 = model.calls[1].prompt
    assert turn2.count('"content": "hello"') == 2 and "at most 2 actions per turn" in turn2
    assert sum(e["kind"] == "tool_call" for e in EventLog.read(tmp_path / "e.jsonl")) == 2
    assert "Turn 1 of 12: 11 turn(s) left" in model.calls[0].prompt and "Turn 2 of 12" in turn2


def test_last_turn_asks_for_the_final_answer(tmp_path):
    model = ScriptedModel().set(AGENT.value, "p", [{"final": {"x": 1}}])
    caller = _caller(model, tmp_path)
    caller.max_tool_iterations = 1
    from magi_system.policy import PolicyEngine
    from magi_system.tools.toolbox import Toolbox
    (tmp_path / "ws").mkdir()
    tb = Toolbox(role=AGENT, workspace=tmp_path / "ws", policy=PolicyEngine("offline-test"))
    assert caller.run_tools(AGENT, purpose="p", round=0, prompt_name="_common", prompt_values={}, body="B",
                            final_schema=A, toolbox=tb, phase="t").x == 1
    assert "last turn. Return the final answer now" in model.calls[0].prompt
