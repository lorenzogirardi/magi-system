"""Subgraph A — intake and evidence gathering."""

from __future__ import annotations

from magi_system.engine.context import RunContext, node
from magi_system.engine.gates import project_gate_problems
from magi_system.engine.helpers import dump, meta
from magi_system.models.common import AgentId, Phase
from magi_system.models.knowledge import Evidence, EvidenceKind, RepositorySnapshot

MANIFESTS = ("Makefile", "package.json", "pyproject.toml", "setup.py", "go.mod", "Cargo.toml", "pom.xml", "build.gradle")


@node("intake")
def intake(state: dict, ctx: RunContext) -> dict:
    commit = state["baseline_commit"]
    problems = project_gate_problems(ctx)
    if problems:  # fail now at zero LLM cost instead of after a full run
        ctx.events.emit("preflight_failed", problems=problems)
        return {"final_status": "UNRESOLVED_REQUIREMENT", "control_fingerprint": ctx.fingerprint(),
                "diagnosis": "mandatory gates cannot run: " + "; ".join(problems)}
    ws = ctx.workspaces
    # Start from the exact baseline, discarding anything a crashed attempt left behind.
    ws.restore(ctx.run_id, commit)
    files = ws.snapshot_files(ctx.run_id, commit)
    paths = [f.path for f in files]
    cfg = ctx.project.config
    snapshot = RepositorySnapshot(
        meta=meta("snap", AgentId.PLATFORM, 0, Phase.INTAKE, commit, input_hash=commit),
        target_identity=state["target_identity"],
        commit=commit,
        files=tuple(files),
        languages=cfg.languages,
        manifests=tuple(p for p in paths if p.rsplit("/", 1)[-1] in MANIFESTS and "/" not in p),
        doc_paths=tuple(p for p in paths if p.endswith(".md") or p.startswith("docs/")),
        test_paths=tuple(p for p in paths if p.startswith(("tests/", "test/", "spec/"))),
        project_config_source=ctx.project.source.value,
    )
    evidence = [
        Evidence(
            meta=meta("ev", AgentId.PLATFORM, 0, Phase.INTAKE, "file", f.path, f.blob, input_hash=commit),
            kind=EvidenceKind.FILE, locator=f.path, summary=f"{f.path} ({f.size} bytes)",
            content_hash=f.blob, target_commit=commit, collected_by_tool="git ls-tree",
        )
        for f in files
        if f.path in snapshot.manifests
    ]
    evidence.append(
        Evidence(
            meta=meta("ev", AgentId.PLATFORM, 0, Phase.INTAKE, "policy", ctx.policy.version, input_hash=state["policy_sha256"]),
            kind=EvidenceKind.POLICY, locator=f"policy@{ctx.policy.version}/{ctx.policy.profile}",
            summary="effective platform policy", content_hash=state["policy_sha256"], target_commit=commit,
            collected_by_tool="policy engine",
        )
    )
    ctx.events.emit("intake", files=len(files), manifests=list(snapshot.manifests), config_source=ctx.project.source.value,
                    inferred=list(ctx.project.inferred), denied_commands=ctx.project.denied_commands)
    return {
        "repository_snapshot": snapshot.model_dump(mode="json"),
        "evidence": dump(evidence),
        "candidate_commit": commit,
        "control_fingerprint": ctx.fingerprint(),
        "routing": [{"from": "intake", "to": "magi", "reason": "snapshot ready"}],
    }
