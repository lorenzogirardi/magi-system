"""Run-scoped dependencies passed to nodes through LangGraph's Runtime context.

Context is NOT checkpointed: on resume the application service rebuilds it from the
run store and verifies compatibility first.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from magi_system.agents.caller import AgentCaller
from magi_system.config.project import ResolvedProjectConfig
from magi_system.engine.budget import BudgetExhausted, BudgetTracker
from magi_system.execution.runner import ExecutionRunner
from magi_system.models.common import AgentId
from magi_system.models.run import RunInput
from magi_system.observability.events import EventLog
from magi_system.policy.engine import CODE_WRITER_ROLES, PolicyEngine
from magi_system.tools.toolbox import Toolbox
from magi_system.workspace.git import GitError
from magi_system.workspace.manager import WorkspaceError, WorkspaceManager


def control_fingerprint() -> dict[str, str]:
    """Fresh (uncached) hashes of protected control-plane assets: policy, prompts and the
    platform code that implements gates and graders.

    Recomputed from disk at gate time and compared with the value recorded at intake.
    """
    root = Path(str(resources.files("magi_system")))
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix in {".py", ".yaml", ".md"} and "__pycache__" not in p.parts:
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@dataclass
class RunContext:
    run_id: str
    run_input: RunInput
    policy: PolicyEngine
    workspaces: WorkspaceManager
    runner: ExecutionRunner
    caller: AgentCaller
    events: EventLog
    budget: BudgetTracker
    project: ResolvedProjectConfig
    fingerprint: Callable[[], dict[str, str]] = control_fingerprint

    @property
    def workspace(self) -> Path:
        return self.workspaces.workspace_path(self.run_id)

    @property
    def doc_scopes(self) -> tuple[str, ...]:
        return self.project.config.doc_paths

    @property
    def test_scopes(self) -> tuple[str, ...]:
        return self.project.config.test_paths or ("tests/**", "test/**")

    def toolbox(self, role: AgentId, *, task_scopes: tuple[str, ...] = (), with_runner: bool = True) -> Toolbox:
        return Toolbox(
            role=role,
            workspace=self.workspace,
            policy=self.policy,
            runner=self.runner if with_runner else None,
            task_scopes=task_scopes,
            doc_scopes=self.doc_scopes,
            test_scopes=self.test_scopes,
            required_scopes=self.project.config.write_scopes if role in CODE_WRITER_ROLES else (),
            on_call=self.budget.on_tool_call,
        )


INFRA_ERRORS = (GitError, WorkspaceError, OSError)


def node(name: str):
    """Wrap a node: lifecycle events, budget checks, and mapping of budget/infra errors
    to terminal statuses. Unknown exceptions propagate (the run can then be resumed)."""

    def decorate(fn: Callable[[dict, RunContext], dict]):
        # LangGraph injects `runtime` by parameter name, found via inspect.signature. Do not
        # use functools.wraps: its `__wrapped__` would expose the inner (state, ctx) signature.
        def wrapped(state: dict, runtime) -> dict:
            ctx = runtime.context
            fanout = state.get("fanout") or {}
            label = f"{name}[{fanout['agent']}]" if fanout.get("agent") else name
            ctx.events.emit("node_start", node=label, round=state.get("architecture_round", 0))
            try:
                ctx.budget.check_time()
                update = fn(state, ctx) or {}
            except BudgetExhausted as exc:
                ctx.events.emit("budget_exhausted", node=label, dimension=exc.dimension, detail=str(exc))
                update = {"final_status": "BUDGET_EXHAUSTED", "diagnosis": f"{label}: {exc}"} if not fanout else {
                    "stage_results": {fanout["key"]: {"ok": False, "budget": True, "detail": str(exc)}}
                }
            except INFRA_ERRORS as exc:
                ctx.events.emit("infra_error", node=label, detail=str(exc)[:1000])
                update = {"final_status": "INFRA_FAILURE", "diagnosis": f"{label}: {exc}"} if not fanout else {
                    "stage_results": {fanout["key"]: {"ok": False, "infra": True, "detail": str(exc)[:1000]}}
                }
            # Everything a node returns is checkpointed: redact secrets before it is persisted.
            update = ctx.events.redactor.value(update)
            update.setdefault("budget_used", {})
            update["budget_used"] = {**ctx.budget.snapshot(), **update["budget_used"]}
            ctx.events.emit("node_end", node=label, final_status=update.get("final_status"))
            return update

        wrapped.__name__ = wrapped.__qualname__ = fn.__name__
        wrapped.__doc__ = fn.__doc__
        return wrapped

    return decorate
