"""Application service: start, resume, status and report of runs.

This is the only entry point interfaces (CLI today) call. It wires workspace, policy,
project config, runner, models and the LangGraph engine, records everything needed for
reproducibility, and turns the final graph state into a typed `RunOutcome`.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

import yaml
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel, ConfigDict

from magi_system import prompts
from magi_system.agents.caller import AgentCaller
from magi_system.config.project import ProjectConfigError, ResolvedProjectConfig, resolve_project_config
from magi_system.engine.budget import BudgetTracker
from magi_system.engine.context import RunContext, control_fingerprint
from magi_system.engine.graph import build_graph
from magi_system.engine.state import INITIAL_KEYS
from magi_system.execution.runner import make_runner
from magi_system.llm.base import ModelAdapter
from magi_system.llm.providers import make_adapter
from magi_system.models.common import AgentId, content_hash
from magi_system.models.run import (
    AgentModelConfig,
    ChangeLevel,
    GateCheck,
    PublicationMode,
    PublicationRecord,
    RunBudget,
    RunInput,
    RunOutcome,
    TerminalStatus,
)
from magi_system.observability.events import EventLog, LangfuseTracer
from magi_system.policy.engine import PolicyEngine, PolicyError
from magi_system.service.store import open_store
from magi_system.workspace.git import git
from magi_system.workspace.manager import WorkspaceError, WorkspaceManager, control_repo_root

RECURSION_LIMIT = 400
AGENT_IDS = tuple(a for a in AgentId if a not in (AgentId.VERIFIER, AgentId.PLATFORM))


class RunInputError(Exception):
    """The run could not start (bad revision, inaccessible target, dirty checkout ...)."""


class ResumeError(Exception):
    """The run cannot be resumed safely (changed prompts/policy, missing workspace ...)."""


class RunSettings(BaseModel):
    """Optional external settings file (`--config`). No secrets: keys come from env vars."""

    model_config = ConfigDict(extra="forbid")

    budget: RunBudget = RunBudget()
    agents: dict[str, AgentModelConfig] = {}
    default_agent: AgentModelConfig = AgentModelConfig()
    langfuse: bool = False

    @classmethod
    def load(cls, path: Path | None) -> RunSettings:
        if path is None:
            return cls()
        return cls.model_validate(yaml.safe_load(path.read_text()) or {})

    def agent(self, agent: AgentId) -> AgentModelConfig:
        return self.agents.get(agent.value, self.default_agent)


def platform_version() -> dict:
    try:
        version = metadata.version("magi-system")
    except metadata.PackageNotFoundError:
        version = "unknown"
    root = control_repo_root()
    r = git("rev-parse", "HEAD", cwd=root, check=False)
    if r.returncode != 0:
        # Not a git checkout (installed wheel, GitHub Action tarball): the caller may say which
        # ref it is. The control_fingerprint content hash stays the authoritative identity.
        return {"version": version, "commit": os.environ.get("MAGI_PLATFORM_REF", "unknown"), "src_dirty": None}
    dirty = git("status", "--porcelain", "--", "src", cwd=root, check=False).stdout.strip()
    return {"version": version, "commit": r.stdout.strip(), "src_dirty": bool(dirty)}


def _fingerprint_hash(fp: dict) -> str:
    return content_hash(fp)


def _identity(repo: str) -> str:
    name = repo.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return name.removesuffix(".git") or "target"


def _image_for(project: ResolvedProjectConfig, policy: PolicyEngine) -> str:
    if project.config.container_image:
        return project.config.container_image
    images = policy.effective["execution"]["container"]["default_images"]
    for lang in project.config.languages:
        if lang in images:
            return images[lang]
        if lang == "javascript":
            return images["node"]
    return images["shell"]


@dataclass
class RunService:
    runs_root: Path
    models: dict[AgentId, ModelAdapter] | None = None  # injected for offline mode/tests

    def __post_init__(self) -> None:
        self.workspaces = WorkspaceManager(self.runs_root)
        self.store = open_store(self.workspaces.runs_root)
        self.graph = build_graph(self.store.checkpointer)

    # -- wiring ---------------------------------------------------------------------------

    def _models(self, settings: RunSettings) -> dict[AgentId, ModelAdapter]:
        if self.models is not None:
            return self.models
        out = {}
        for agent in AGENT_IDS:
            cfg = settings.agent(agent)
            out[agent] = make_adapter(cfg.provider, cfg.model, temperature=cfg.temperature, max_output_tokens=cfg.max_output_tokens,
                                      routing=cfg.routing, reasoning=cfg.reasoning)
        return out

    def _context(self, run_id: str, run_input: RunInput, settings: RunSettings, project: ResolvedProjectConfig,
                 started_at: float, runner_choice: str | None) -> RunContext:
        policy = PolicyEngine(run_input.policy_profile)
        events = EventLog(self.workspaces.run_dir(run_id) / "events.jsonl", run_id,
                          tracer=LangfuseTracer(run_id) if settings.langfuse else None)
        budget = BudgetTracker(run_input.budget, started_at=started_at)
        runner = make_runner(policy, self.workspaces.workspace_path(run_id), runner=runner_choice, image=_image_for(project, policy))
        caller = AgentCaller(
            models=self._models(settings),
            timeouts={a: settings.agent(a).timeout_seconds for a in AGENT_IDS},
            budget=budget, events=events,
            max_tool_iterations=int(policy.effective["limits"]["max_agent_tool_iterations"]),
            max_actions_per_turn=int(policy.effective["limits"]["max_agent_actions_per_turn"]),
        )
        return RunContext(run_id=run_id, run_input=run_input, policy=policy, workspaces=self.workspaces, runner=runner,
                          caller=caller, events=events, budget=budget, project=project)

    # -- start ----------------------------------------------------------------------------

    def start(
        self,
        *,
        repo: str,
        ref: str,
        request_text: str,
        profile: str = "default",
        settings: RunSettings | None = None,
        project_identity: str | None = None,
        include_uncommitted: bool = False,
        publication: PublicationMode = PublicationMode.LOCAL_CANDIDATE,
        runner: str | None = None,
        run_id: str | None = None,
    ) -> RunOutcome:
        settings = settings or RunSettings()
        try:
            policy = PolicyEngine(profile)
        except PolicyError as exc:
            raise RunInputError(str(exc)) from exc
        if not request_text.strip():
            raise RunInputError("the change request is empty")
        if not policy.publication_allowed(publication.value):
            raise RunInputError(f"publication mode {publication.value!r} is not allowed by policy profile {profile!r}")
        if runner and not policy.runner_allowed(runner):
            raise RunInputError(f"runner {runner!r} is not allowed by policy profile {profile!r}")
        run_id = run_id or f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        try:
            manifest = self.workspaces.prepare(run_id, repo, ref, include_uncommitted=include_uncommitted)
        except WorkspaceError as exc:
            raise RunInputError(str(exc)) from exc

        request_sha = hashlib.sha256(request_text.encode()).hexdigest()
        (self.workspaces.run_dir(run_id) / "inputs" / "request.md").write_text(request_text)
        run_input = RunInput(
            repo=self.workspaces.locator_model(manifest), requested_ref=ref, resolved_commit=manifest.resolved_commit,
            request_text=request_text, request_sha256=request_sha, project_identity=project_identity or _identity(manifest.locator),
            policy_profile=profile, budget=settings.budget,
            agents={a.value: settings.agent(a) for a in AGENT_IDS}, publication=publication,
            include_uncommitted=include_uncommitted, uncommitted_patch_sha256=manifest.uncommitted_patch_sha256,
        )
        started_at = time.time()
        record = {
            "run_input": run_input.model_dump(mode="json"),
            "settings": settings.model_dump(mode="json"),
            "runner": runner,
            "started_at": started_at,
            "versions": {
                "platform": platform_version(),
                "prompts": prompts.manifest(),
                "policy": {"version": policy.version, "profile": profile, "sha256": policy.snapshot().sha256},
                "control_fingerprint": _fingerprint_hash(control_fingerprint()),
            },
            "target": {"identity": run_input.project_identity, "locator": manifest.locator,
                       "resolved_commit": manifest.resolved_commit, "baseline_commit": manifest.baseline_commit},
        }
        try:
            project = resolve_project_config(self.workspaces.workspace_path(run_id), policy)
        except ProjectConfigError as exc:
            self.store.create(run_id, {**record, "project_config": None})
            return self._finish_without_graph(run_id, run_input, manifest.baseline_commit, TerminalStatus.POLICY_DENIED,
                                              f"project configuration rejected: {exc}")
        record["project_config"] = project.model_dump(mode="json")
        ctx = self._context(run_id, run_input, settings, project, started_at, runner)
        # A missing image or sandbox configuration would otherwise surface as failing checks
        # and send developers chasing a non-existent bug. Check before any agent runs.
        if not ctx.runner.available():
            hint = ctx.runner.unavailable_hint()
            ctx.runner.close()
            self.workspaces.cleanup(run_id, keep_artifacts=False)
            raise RunInputError(hint)
        self.store.create(run_id, record)
        record_env = {"environment_id": ctx.runner.environment_id()}
        self.store.update(run_id, **record_env)
        ctx.events.emit("run_start", run_id=run_id, target=run_input.project_identity, commit=manifest.baseline_commit,
                        profile=profile, runner=ctx.runner.name, environment=record_env["environment_id"],
                        prompts=record["versions"]["prompts"], policy=record["versions"]["policy"])
        state = {
            **INITIAL_KEYS,
            "run_id": run_id,
            "original_request": {"text": request_text, "sha256": request_sha, "source": "request-file"},
            "baseline_commit": manifest.baseline_commit,
            "target_identity": run_input.project_identity,
            "policy_version": policy.version,
            "policy_sha256": policy.snapshot().sha256,
            "project_config": project.config.model_dump(mode="json"),
        }
        return self._drive(run_id, ctx, state)

    # -- resume ---------------------------------------------------------------------------

    def _check_resumable(self, run_id: str, record: dict) -> None:
        versions = record["versions"]
        problems = []
        if prompts.manifest() != versions["prompts"]:
            problems.append("prompt versions differ from the ones recorded for this run")
        policy = PolicyEngine(versions["policy"]["profile"])
        if policy.snapshot().sha256 != versions["policy"]["sha256"]:
            problems.append("policy differs from the one recorded for this run")
        if _fingerprint_hash(control_fingerprint()) != versions["control_fingerprint"]:
            problems.append("magi-system code, prompts or policy changed since the run started")
        ws = self.workspaces.workspace_path(run_id)
        if not (ws / ".git").is_dir():
            problems.append("run workspace is missing")
        else:
            candidate = self._values(run_id).get("candidate_commit") or record["target"]["baseline_commit"]
            if not self.workspaces.commit_exists(run_id, candidate):
                problems.append(f"candidate {candidate[:12]} recorded in the checkpoint is not in the workspace")
            if not self.workspaces.commit_exists(run_id, record["target"]["baseline_commit"]):
                problems.append("baseline commit is not in the workspace")
        if problems:
            raise ResumeError("; ".join(problems))

    def resume(self, run_id: str) -> RunOutcome:
        try:
            record = self.store.get(run_id)
        except KeyError as exc:
            raise ResumeError(f"unknown run {run_id}") from exc
        if record.get("outcome") and record["status"] in TerminalStatus.__members__ and not record.get("resumable"):
            return RunOutcome.model_validate(record["outcome"])
        self._check_resumable(run_id, record)
        run_input = RunInput.model_validate(record["run_input"])
        settings = RunSettings.model_validate(record["settings"])
        project = ResolvedProjectConfig.model_validate(record["project_config"])
        ctx = self._context(run_id, run_input, settings, project, record["started_at"], record.get("runner"))
        ctx.events.emit("run_resume", run_id=run_id)
        return self._drive(run_id, ctx, None)

    # -- execution & outcome --------------------------------------------------------------

    def _drive(self, run_id: str, ctx: RunContext, state: dict | None) -> RunOutcome:
        config = {"configurable": {"thread_id": run_id}, "recursion_limit": RECURSION_LIMIT}
        lock = self.workspaces.lock(run_id)
        try:
            try:
                final = self.graph.invoke(state, config, context=ctx)
            except GraphRecursionError:
                final = {**(self.graph.get_state(config).values or {}), "final_status": TerminalStatus.BUDGET_EXHAUSTED.value,
                         "diagnosis": f"graph step limit ({RECURSION_LIMIT}) reached"}
            except Exception as exc:  # crash: checkpoint kept, run can be resumed
                ctx.events.emit("run_crashed", error=f"{type(exc).__name__}: {exc}"[:2000])
                values = self._values(run_id)
                outcome = self._outcome(run_id, ctx, {**values, "final_status": TerminalStatus.INFRA_FAILURE.value,
                                                      "diagnosis": f"run interrupted by {type(exc).__name__}: {exc}; resumable with `magi resume`"},
                                        publish=False)
                self.store.update(run_id, status=outcome.status.value, outcome=outcome.model_dump(mode="json"), resumable=True)
                return outcome
        finally:
            lock.close()
            ctx.runner.close()
        outcome = self._outcome(run_id, ctx, final, publish=True)
        self.store.update(run_id, status=outcome.status.value, outcome=outcome.model_dump(mode="json"), resumable=False)
        ctx.events.emit("run_end", status=outcome.status.value, final_commit=outcome.final_commit, change_level=outcome.change_level.value)
        self._write_report(run_id)
        return outcome

    def _publish(self, run_id: str, ctx: RunContext, base: str, head: str) -> list[PublicationRecord]:
        """Local-only publication, idempotent by dedup key. Push/PR are never done here."""
        records = []
        if head == base:
            return records
        key = f"export_patch:{run_id}:{head}"
        # Writing the patch is idempotent (same base/head → same content); the dedup record
        # makes the effect auditable and is inserted at most once.
        path = self.workspaces.export_patch(run_id, base, head, "candidate.patch")
        self.store.record_effect(key, run_id, str(path))
        records.append(PublicationRecord(operation="export_patch", performed=True,
                                         location=str(path.relative_to(self.workspaces.run_dir(run_id))), dedup_key=key))
        records.append(PublicationRecord(operation="local_branch", performed=True,
                                         location=f"{self.workspaces.workspace_path(run_id).relative_to(self.workspaces.runs_root)}#magi/{run_id}",
                                         dedup_key=f"local_branch:{run_id}:{head}"))
        ctx.events.emit("publication", operations=[r.operation for r in records], commit=head)
        return records

    def _outcome(self, run_id: str, ctx: RunContext, state: dict, *, publish: bool) -> RunOutcome:
        status = TerminalStatus(state.get("final_status") or TerminalStatus.INFRA_FAILURE.value)
        base = state.get("baseline_commit") or ctx.run_input.resolved_commit
        head = state.get("candidate_commit") or base
        checks = tuple(GateCheck.model_validate(c) for c in state.get("gate_checks") or [])
        publications = tuple(self._publish(run_id, ctx, base, head)) if publish and status is TerminalStatus.SUCCESS else ()
        docs = sorted({p for d in (state.get("documentation_results") or {}).values() for p in d.get("changed", [])})
        return RunOutcome(
            run_id=run_id, target_identity=ctx.run_input.project_identity, baseline_commit=base,
            final_commit=head if status is TerminalStatus.SUCCESS else "", plan_version=state.get("plan_version", 0),
            status=status, diagnosis=state.get("diagnosis", ""), gate_checks=checks,
            change_level=ChangeLevel.VERIFIED if status is TerminalStatus.SUCCESS else ChangeLevel.NONE,
            diff_ref="artifacts/candidate.patch" if publications else "", patch_ref="artifacts/candidate.patch" if publications else "",
            documentation_refs=tuple(docs), evidence_refs=tuple(c.evidence_ref for c in checks if c.evidence_ref),
            publications=publications,
        )

    def _finish_without_graph(self, run_id: str, run_input: RunInput, baseline: str, status: TerminalStatus, diagnosis: str) -> RunOutcome:
        outcome = RunOutcome(run_id=run_id, target_identity=run_input.project_identity, baseline_commit=baseline, final_commit="",
                             plan_version=0, status=status, diagnosis=diagnosis, gate_checks=(), change_level=ChangeLevel.NONE)
        EventLog(self.workspaces.run_dir(run_id) / "events.jsonl", run_id).emit("run_end", status=status.value, diagnosis=diagnosis)
        self.store.update(run_id, status=status.value, outcome=outcome.model_dump(mode="json"), resumable=False)
        self._write_report(run_id)
        return outcome

    # -- status & report ------------------------------------------------------------------

    def _values(self, run_id: str) -> dict:
        """Latest state values, descending into an interrupted subgraph if one is pending
        (a crash inside a subgraph leaves the newest values in the subgraph checkpoint)."""
        snap = self.graph.get_state({"configurable": {"thread_id": run_id}}, subgraphs=True)
        values = dict(snap.values or {})
        while snap.tasks and snap.tasks[0].state is not None:
            snap = snap.tasks[0].state
            values.update(snap.values or {})
        return values

    def status(self, run_id: str) -> dict:
        record = self.store.get(run_id)
        values = self._values(run_id)
        trail = values.get("routing") or []
        return {
            "run_id": run_id,
            "status": record["status"],
            "resumable": record.get("resumable", False),
            "target": record["target"],
            "architecture_round": values.get("architecture_round"),
            "plan_version": values.get("plan_version"),
            "selected_plan": values.get("selected_plan"),
            "candidate_commit": values.get("candidate_commit"),
            "remediation_cycle": values.get("remediation_cycle"),
            "last_routing": trail[-1] if trail else None,
            "budget_used": values.get("budget_used"),
            "diagnosis": (record.get("outcome") or {}).get("diagnosis", ""),
        }

    def report(self, run_id: str) -> dict:
        record = self.store.get(run_id)
        values = self._values(run_id)
        findings = sorted(
            ({k: f[k] for k in ("title", "severity", "category", "status", "status_reason", "subject_id")}
             | {"id": f["meta"]["id"], "producer": f["meta"]["producer"]}
             for f in (values.get("findings") or {}).values()),
            key=lambda f: f["id"],
        )
        outcome = record.get("outcome") or {}
        return {
            "outcome": outcome,
            "change_levels": {
                "verified": outcome.get("change_level") == ChangeLevel.VERIFIED.value,
                "integrated": False,  # merge is not implemented and never performed by the platform
                "deployed": False,  # deployment is not implemented and never performed by the platform
            },
            "target": record["target"],
            "versions": record.get("versions"),
            "environment_id": record.get("environment_id"),
            "project_config": record.get("project_config"),
            "selected_plan": values.get("selected_plan"),
            "decisions": values.get("decisions"),
            "findings": findings,
            "budget_used": values.get("budget_used"),
            "routing": values.get("routing"),
            "notice": "Offline/scripted model runs demonstrate orchestration only, not model quality."
            if any(c.get("provider") == "fake" for c in (record.get("run_input") or {}).get("agents", {}).values()) else "",
        }

    def _write_report(self, run_id: str) -> None:
        rep = self.report(run_id)
        art = self.workspaces.artifacts_path(run_id)
        (art / "report.json").write_text(json.dumps(rep, indent=2, sort_keys=True, default=str))
        (art / "report.md").write_text(render_report_md(rep))


def render_report_md(rep: dict) -> str:
    o = rep["outcome"]
    lines = [
        f"# MAGI run report — {o.get('run_id', '')}",
        "",
        f"- Target: `{rep['target']['identity']}` ({rep['target']['locator']})",
        f"- Baseline commit: `{o.get('baseline_commit', '')}`",
        f"- Status: **{o.get('status', '')}**",
        f"- Final candidate: `{o.get('final_commit') or '—'}` (plan version {o.get('plan_version', 0)})",
        f"- Verified change: {'yes' if rep['change_levels']['verified'] else 'no'} · "
        f"Integrated (merged): no · Deployed: no",
        f"- Diagnosis: {o.get('diagnosis', '')}",
        "",
    ]
    if rep.get("notice"):
        lines += [f"> {rep['notice']}", ""]
    lines += ["## Mandatory gates", "", "| Gate | Verdict | Commit | Detail |", "|---|---|---|---|"]
    for c in o.get("gate_checks", []):
        detail = c["detail"].splitlines()[0][:120] if c["detail"] else ""
        lines.append(f"| {c['name']} | {c['verdict']} | `{c['candidate_commit'][:12]}` | {detail} |")
    lines += ["", "## Publication actually performed", ""]
    pubs = o.get("publications", [])
    lines += [f"- {p['operation']}: {p['location']}" for p in pubs] or ["- none"]
    lines += ["", "No push, pull request, merge or deployment was performed.", "", "## Findings", ""]
    lines += [f"- [{f['status']}] {f['severity']} {f['category']} — {f['title']} ({f['producer']})" for f in rep["findings"]] or ["- none"]
    return "\n".join(lines) + "\n"
