"""Invoke an agent: build prompt, call the model with a timeout, validate structured output,
optionally run a bounded tool loop. Failures are returned as typed `AgentFailure`s."""

from __future__ import annotations

import concurrent.futures
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from magi_system import prompts
from magi_system.engine.budget import BudgetExhausted, BudgetTracker
from magi_system.engine.schemas import turn_schema
from magi_system.llm.base import (
    InvalidStructuredOutput,
    ModelAdapter,
    ModelProviderError,
    ModelRequest,
    ModelTimeout,
    parse_structured,
)
from magi_system.models.common import AgentId
from magi_system.models.decision import AgentFailure, FailureKind
from magi_system.observability.events import EventLog
from magi_system.tools.toolbox import Toolbox, ToolError

UNTRUSTED_OPEN = "<untrusted-data>"
UNTRUSTED_CLOSE = "</untrusted-data>"


def untrusted(payload: Any) -> str:
    """Wrap repository/tool content so prompts mark it as data, never instructions."""
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=1, default=str)
    text = text.replace(UNTRUSTED_CLOSE, "</untrusted-data_>")
    return f"{UNTRUSTED_OPEN}\n{text}\n{UNTRUSTED_CLOSE}"


@dataclass
class AgentCaller:
    models: dict[AgentId, ModelAdapter]
    timeouts: dict[AgentId, float]
    budget: BudgetTracker
    events: EventLog
    max_tool_iterations: int = 12
    max_invalid_retries: int = 1

    def _model(self, agent: AgentId) -> ModelAdapter:
        model = self.models.get(agent)
        if model is None:
            raise ModelProviderError(f"no model configured for {agent.value}")
        return model

    def _complete(self, agent: AgentId, request: ModelRequest) -> str:
        self.budget.before_llm_call()
        timeout = self.timeouts.get(agent, 120.0)
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = pool.submit(self._model(agent).complete, request, timeout=timeout)
        try:
            response = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            raise ModelTimeout(f"no response within {timeout}s") from exc
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        self.budget.after_llm_call(response.input_tokens, response.output_tokens, response.cost_usd)
        self.events.emit(
            "llm_call", agent=agent.value, purpose=request.purpose, round=request.round,
            prompt=request.prompt_version, input_tokens=response.input_tokens, output_tokens=response.output_tokens,
        )
        return response.text

    def ask[T: BaseModel](
        self,
        agent: AgentId,
        *,
        purpose: str,
        round: int,
        prompt_name: str,
        prompt_values: dict[str, str],
        body: str,
        schema: type[T],
        phase: str,
    ) -> T | AgentFailure:
        """Single structured call (no tools). One retry on invalid output."""
        common, prompt = prompts.load("_common"), prompts.load(prompt_name)
        request = ModelRequest(
            agent=agent.value, purpose=purpose, round=round,
            system=common.render() + "\n\n" + prompt.render(**prompt_values),
            prompt=body, schema_name=schema.__name__, json_schema=schema.model_json_schema(),
            prompt_version=prompt.ref,
        )
        last = ""
        for _attempt in range(self.max_invalid_retries + 1):
            try:
                return parse_structured(self._complete(agent, request), schema)
            except InvalidStructuredOutput as exc:
                last = str(exc)
                self.events.emit("agent_invalid_output", agent=agent.value, purpose=purpose, detail=last[:500])
            except ModelTimeout as exc:
                return self._fail(agent, round, phase, FailureKind.TIMEOUT, str(exc))
            except ModelProviderError as exc:
                return self._fail(agent, round, phase, FailureKind.PROVIDER_ERROR, str(exc))
        return self._fail(agent, round, phase, FailureKind.INVALID_OUTPUT, last)

    def run_tools[T: BaseModel](
        self,
        agent: AgentId,
        *,
        purpose: str,
        round: int,
        prompt_name: str,
        prompt_values: dict[str, str],
        body: str,
        final_schema: type[T],
        toolbox: Toolbox,
        phase: str,
        on_turn: Callable[[int], None] | None = None,
    ) -> T | AgentFailure:
        """Bounded tool loop: each turn returns tool actions or a final answer."""
        turn_model = turn_schema(final_schema)
        transcript: list[str] = [body, f"Available tools: {json.dumps(toolbox.available())}"]
        for i in range(self.max_tool_iterations):
            if on_turn:
                on_turn(i)
            result = self.ask(
                agent, purpose=purpose, round=round, prompt_name=prompt_name, prompt_values=prompt_values,
                body="\n\n".join(transcript), schema=turn_model, phase=phase,
            )
            if isinstance(result, AgentFailure):
                return result
            if result.final is not None and not result.actions:
                return result.final
            outputs = []
            for action in result.actions:
                try:
                    out = toolbox.call(action.tool, action.args)
                except ToolError as exc:
                    out = {"error": str(exc)}
                except BudgetExhausted:
                    raise
                self.events.emit("tool_call", agent=agent.value, tool=action.tool, args=_safe_args(action.args), ok="error" not in out)
                outputs.append({"tool": action.tool, "result": out})
            transcript.append("Tool results:\n" + untrusted(outputs))
        return self._fail(agent, round, phase, FailureKind.INVALID_OUTPUT, "tool loop exceeded max iterations")

    def _fail(self, agent: AgentId, round: int, phase: str, kind: FailureKind, detail: str) -> AgentFailure:
        self.events.emit("agent_failure", agent=agent.value, round=round, phase=phase, failure=kind.value, detail=detail[:500])
        return AgentFailure(agent=agent, round=round, phase=phase, kind=kind, detail=detail[:2000])


def _safe_args(args: dict) -> dict:
    return {k: (f"<{len(str(v))} chars>" if k == "content" else v) for k, v in args.items()}
