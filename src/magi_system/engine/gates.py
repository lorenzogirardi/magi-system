"""Mechanical checks on a candidate: project commands, acceptance commands, integrity and
write-scope checks, and scope-safe commits. All results are bound to one commit."""

from __future__ import annotations

import re

from magi_system.engine.context import RunContext
from magi_system.engine.helpers import meta
from magi_system.models.common import AgentId, Phase, Verdict
from magi_system.models.decision import VerificationResult
from magi_system.policy.engine import matches_any
from magi_system.workspace.git import git

PROJECT_GATES = {"project.build": "build", "project.test": "test", "project.lint": "lint"}


def gate_result(gate: str, commit: str, verdict: Verdict, detail: str, *, round: int, phase: Phase,
                command: str = "", exit_code: int | None = None, producer: AgentId = AgentId.PLATFORM, salt: str = "") -> VerificationResult:
    return VerificationResult(
        meta=meta("gate", producer, round, phase, gate, commit, salt, input_hash=commit),
        subject_id=gate, check=gate, verdict=verdict, detail=detail[:4000], candidate_commit=commit,
        command=command, exit_code=exit_code,
    )


_TEXT_SEARCH_CMD = re.compile(r"(^|[;&|(]\s*)(grep|egrep|fgrep|rg|ack)\b")
_FILE_WALK = re.compile(r"\b(rglob|glob|walk|iterdir)\(")
_FILE_READ = re.compile(r"\bread_text\(|\.read\(\)|\bopen\(")


def is_source_text_scan(command: str) -> bool:
    """True when the command searches the TEXT of source files (grep, or files read through a glob).

    Such a check also matches comments, docstrings, field names and messages, so a hit proves nothing about
    behaviour: the docstring "no eval/exec" fails a search for `exec`, and a Kubernetes `requests` field fails a
    search for the network library. An AST-based check reads text too but understands code, so it is not one."""
    if re.search(r"\bast\.|\bimport ast\b", command):
        return False
    return bool(_TEXT_SEARCH_CMD.search(command) or (_FILE_WALK.search(command) and _FILE_READ.search(command)))


def scenario_verdict(command: str, verdict: Verdict, exit_code: int | None, stdout: str) -> tuple[Verdict, str]:
    """Acceptance scenarios are written by a model before the code exists; a failure that does not come from running
    the code must not decide the run. Returns the verdict to record and a note ("" = unchanged)."""
    if verdict is not Verdict.FAIL:
        return verdict, ""
    if exit_code == 5 and "pytest" in command:
        return Verdict.NOT_APPLICABLE, "advisory: pytest selected no tests (exit code 5), so the scenario verified nothing"
    if is_source_text_scan(command):
        return Verdict.NOT_APPLICABLE, ("advisory: the scenario searches source TEXT (also comments and names), not behaviour; "
                                        f"it matched: {stdout.strip()[-400:] or '(no output)'}")
    return verdict, ""


def run_command_check(ctx: RunContext, gate: str, command: str, commit: str, *, round: int, phase: Phase, salt: str = "",
                      scenario: bool = False) -> VerificationResult:
    ctx.budget.on_tool_call("run_command")
    r = ctx.runner.run(command, cwd=ctx.workspace)
    if r.denied:
        verdict, detail = Verdict.UNKNOWN, f"not executed: command denied by policy ({r.denial_reason})"
    elif r.timed_out:
        verdict, detail = Verdict.ERROR, "command timed out"
    elif r.exit_code in (125, 126, 127) and not r.stdout and "not found" in r.stderr.lower():
        verdict, detail = Verdict.ERROR, f"environment error: {r.stderr.strip()[:500]}"
    else:
        verdict = Verdict.PASS if r.exit_code == 0 else Verdict.FAIL
        detail = f"exit {r.exit_code}\n--- stdout ---\n{r.stdout[-3000:]}\n--- stderr ---\n{r.stderr[-1500:]}"
        if scenario:
            verdict, note = scenario_verdict(command, verdict, r.exit_code, r.stdout)
            if note:
                detail = f"{note}\n{detail}"
                ctx.events.emit("scenario_advisory", gate=gate, commit=commit, command=command, exit_code=r.exit_code, note=note[:500])
    ctx.events.emit("check", gate=gate, commit=commit, verdict=verdict.value, command=command, exit_code=r.exit_code,
                    runner=r.runner, environment=r.environment_id)
    return gate_result(gate, commit, verdict, detail, round=round, phase=phase, command=command, exit_code=r.exit_code, salt=salt)


def project_gate_problems(ctx: RunContext) -> list[str]:
    """Mandatory project gates that cannot run (no command, denied, no allowed N/A): known before any LLM call."""
    cfg, problems = ctx.project.config, []
    for gate, key in PROJECT_GATES.items():
        if gate not in ctx.policy.mandatory_gates:
            continue
        if key in ctx.project.denied_commands:
            problems.append(f"{gate}: command not authorized ({ctx.project.denied_commands[key]})")
        elif key not in cfg.commands and not (cfg.not_applicable.get(gate) and ctx.policy.not_applicable_allowed(gate)):
            problems.append(f"{gate}: no '{key}' command discovered; add commands.{key} (or not_applicable.{gate} with a reason) to .magi/config.yaml")
    return problems


def project_gate(ctx: RunContext, gate: str, commit: str, *, round: int, phase: Phase, salt: str = "") -> VerificationResult:
    key = PROJECT_GATES[gate]
    cfg = ctx.project.config
    if key in ctx.project.denied_commands:
        return gate_result(gate, commit, Verdict.UNKNOWN, f"project command not authorized: {ctx.project.denied_commands[key]}",
                           round=round, phase=phase, salt=salt)
    cmd = cfg.commands.get(key)
    if cmd is None:
        reason = cfg.not_applicable.get(gate, "")
        if reason and ctx.policy.not_applicable_allowed(gate):
            return gate_result(gate, commit, Verdict.NOT_APPLICABLE, f"not applicable: {reason}", round=round, phase=phase, salt=salt)
        return gate_result(gate, commit, Verdict.UNKNOWN, f"no '{key}' command configured or discovered; check not executed",
                           round=round, phase=phase, salt=salt)
    return run_command_check(ctx, gate, cmd.run, commit, round=round, phase=phase, salt=salt)


def allowed_write_globs(ctx: RunContext, plan: dict | None) -> tuple[str, ...]:
    planned = tuple(ch["path"] for ch in (plan or {}).get("changes", []))
    return planned + tuple(ctx.test_scopes) + tuple(ctx.doc_scopes)


def integrity_check(ctx: RunContext, state: dict, commit: str, *, round: int, phase: Phase, salt: str = "") -> VerificationResult:
    """Protected paths untouched, mandatory tests not deleted, control plane unchanged."""
    problems = []
    for status, path in ctx.workspaces.changed_files(ctx.run_id, state["baseline_commit"], commit):
        if matches_any(path, ctx.policy.protected_paths):
            problems.append(f"protected path changed: {path}")
        if status == "D" and matches_any(path, ctx.test_scopes):
            problems.append(f"test deleted: {path}")
    recorded, current = state.get("control_fingerprint") or {}, ctx.fingerprint()
    if not recorded:
        problems.append("control fingerprint missing from run state")
    elif recorded != current:
        changed = sorted(k for k in set(recorded) | set(current) if recorded.get(k) != current.get(k))
        problems.append(f"control plane modified during run: {changed[:10]}")
    verdict = Verdict.FAIL if problems else Verdict.PASS
    ctx.events.emit("check", gate="integrity.protected_paths", commit=commit, verdict=verdict.value, problems=problems)
    return gate_result("integrity.protected_paths", commit, verdict, "; ".join(problems) or "no protected change",
                       round=round, phase=phase, salt=salt)


def scope_check(ctx: RunContext, state: dict, commit: str, *, round: int, phase: Phase, salt: str = "") -> VerificationResult:
    plan = (state.get("proposals") or {}).get(state.get("selected_plan") or "")
    globs = allowed_write_globs(ctx, plan)
    outside = [p for _, p in ctx.workspaces.changed_files(ctx.run_id, state["baseline_commit"], commit) if not matches_any(p, globs)]
    if ctx.project.config.write_scopes:
        outside += [p for _, p in ctx.workspaces.changed_files(ctx.run_id, state["baseline_commit"], commit)
                    if not matches_any(p, ctx.project.config.write_scopes + ctx.doc_scopes) and p not in outside]
    verdict = Verdict.FAIL if outside else Verdict.PASS
    ctx.events.emit("check", gate="scope.write_scopes", commit=commit, verdict=verdict.value, outside=outside)
    return gate_result("scope.write_scopes", commit, verdict,
                       f"changes outside authorized scopes: {outside}" if outside else "all changes inside authorized scopes",
                       round=round, phase=phase, salt=salt)


def revert_out_of_scope(ctx: RunContext, base: str, allowed: tuple[str, ...], *, docs_only: bool = False) -> list[str]:
    """Undo working-tree changes outside `allowed` or on protected paths (e.g. made by a
    command an agent ran). Returns the reverted paths."""
    ws = ctx.workspace
    out = git("status", "--porcelain", "--untracked-files=all", "-z", cwd=ws).stdout
    reverted = []
    for rec in filter(None, out.split("\0")):
        path = rec[3:]
        bad = matches_any(path, ctx.policy.protected_paths) or not matches_any(path, allowed)
        if not docs_only and ctx.project.config.write_scopes and not matches_any(path, ctx.project.config.write_scopes):
            bad = True
        if bad:
            if rec[:2] == "??":
                (ws / path).unlink(missing_ok=True)
            else:
                git("checkout", base, "--", path, cwd=ws)
            reverted.append(path)
    if reverted:
        ctx.events.emit("policy_violation", violation="out_of_scope_write_reverted", paths=reverted)
    return reverted
