"""Resume, reducers, isolation and architecture escalation (brief §14 #15-17; §9; §12)."""

import threading

import pytest

from magi_system.engine.state import append_unique, max_counters, merge_by_id
from magi_system.models.common import AgentId
from magi_system.offline import happy_path
from magi_system.service import ResumeError
from tests.integration.harness import Run


class Crash(Exception):
    pass


def crash_once(responder, flag):
    def respond(req):
        if not flag["done"]:
            flag["done"] = True
            raise Crash("simulated process crash")
        return responder(req)
    return respond


def test_resume_after_crash_does_not_duplicate_effects(tmp_path, monkeypatch):
    scripts = happy_path()
    flag = {"done": False}
    # crash inside the pair step, after DEV1 already committed its change
    scripts[(AgentId.DEV2.value, "pair_assessment")] = crash_once(scripts[(AgentId.DEV2.value, "pair_assessment")], flag)
    r = Run(tmp_path, scripts)
    out = r.start()
    assert out.status.value == "INFRA_FAILURE" and "resumable" in out.diagnosis
    assert r.service.status(r.run_id)["resumable"] is True
    writer_calls = len(r.calls(AgentId.DEV1))
    dev1_commits = r.service.workspaces.find_step_commit(r.run_id, "dev.writer:p1:c0")
    out2 = r.service.resume(r.run_id)
    assert out2.status.value == "SUCCESS"
    assert len(r.calls(AgentId.DEV1)) == writer_calls  # the writer was not re-run
    assert r.service.workspaces.find_step_commit(r.run_id, "dev.writer:p1:c0") == dev1_commits
    from magi_system.workspace import git

    log = git("log", "--format=%s", cwd=r.service.workspaces.workspace_path(r.run_id)).stdout
    assert log.count("magi: dev.writer changes") == 1
    # resuming a finished run returns the recorded outcome without re-running anything
    calls = len(r.model.calls)
    assert r.service.resume(r.run_id).final_commit == out2.final_commit
    assert len(r.model.calls) == calls


def test_writer_step_crash_after_commit_reuses_commit(tmp_path):
    """Node crashes after its commit but before its state update was checkpointed."""
    scripts = happy_path()
    flag = {"done": False}
    # The pair assessment runs right after dev_initial_checks; crash the checks node instead.
    r = Run(tmp_path, scripts)
    import magi_system.engine.nodes_impl as impl

    original = impl.project_gate

    def flaky(*a, **kw):
        if not flag["done"]:
            flag["done"] = True
            raise Crash("crash during initial checks")
        return original(*a, **kw)

    impl.project_gate = flaky
    try:
        r.start()
    finally:
        impl.project_gate = original
    out = r.service.resume(r.run_id)
    assert out.status.value == "SUCCESS"
    assert len(r.calls(AgentId.DEV1, "dev_writer")) == 2  # tools turn + final turn, once


def test_resume_refused_when_workspace_missing(tmp_path):
    scripts = happy_path()
    flag = {"done": False}
    scripts[(AgentId.DEV2.value, "pair_assessment")] = crash_once(scripts[(AgentId.DEV2.value, "pair_assessment")], flag)
    r = Run(tmp_path, scripts)
    r.start()
    r.service.workspaces.cleanup(r.run_id)
    with pytest.raises(ResumeError, match="workspace is missing"):
        r.service.resume(r.run_id)


def test_resume_refused_when_candidate_differs(tmp_path):
    scripts = happy_path()
    flag = {"done": False}
    scripts[(AgentId.DEV2.value, "pair_assessment")] = crash_once(scripts[(AgentId.DEV2.value, "pair_assessment")], flag)
    r = Run(tmp_path, scripts)
    r.start()
    import shutil

    from magi_system.workspace import git

    # Replace the workspace with a fresh clone of the target: the candidate recorded in the
    # checkpoint does not exist there (a different workspace than the one the run used).
    ws = r.service.workspaces.workspace_path(r.run_id)
    head = r.state["candidate_commit"]
    shutil.rmtree(ws)
    git("clone", "--quiet", str(r.repo), str(ws))
    assert not r.service.workspaces.commit_exists(r.run_id, head)
    with pytest.raises(ResumeError, match="candidate"):
        r.service.resume(r.run_id)


def test_resume_refused_when_prompts_or_policy_changed(tmp_path):
    scripts = happy_path()
    flag = {"done": False}
    scripts[(AgentId.DEV2.value, "pair_assessment")] = crash_once(scripts[(AgentId.DEV2.value, "pair_assessment")], flag)
    r = Run(tmp_path, scripts)
    r.start()
    rec = r.service.store.get(r.run_id)
    rec["versions"]["prompts"]["dev_pair"] = "dev_pair@v0#000000000000"
    r.service.store.update(r.run_id, versions=rec["versions"])
    with pytest.raises(ResumeError, match="prompt versions differ"):
        r.service.resume(r.run_id)


def test_reducers_concurrent_updates_lose_nothing():
    left = {}
    lock = threading.Lock()

    def writer(i):
        nonlocal left
        update = {f"r0:agent{i}": {"v": i}}
        with lock:
            left = merge_by_id(left, update)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(left) == 50
    # idempotent re-delivery (subgraph → parent, resume)
    assert merge_by_id(left, dict(left)) == left
    # explicit invalidation
    assert "r0:agent1" not in merge_by_id(left, {"r0:agent1": None})
    assert max_counters({"a": 3}, {"a": 2, "b": 1}) == {"a": 3, "b": 1}
    assert append_unique([1, 2], [2, 3]) == [1, 2, 3]


def test_fan_in_waits_for_all_three_magi_results(tmp_path):
    r = Run(tmp_path)
    r.start()
    collect = [e for e in r.events("node_start") if e["node"] == "magi_analysis_collect"]
    analyses = [e for e in r.events("node_end") if e["node"].startswith("magi_analyze[")]
    assert len(analyses) == 3 and len(collect) == 1
    assert len([k for k in r.state["stage_results"] if k.startswith("analysis:r0:")]) == 3


def test_architecture_escalation_starts_a_new_round_and_plan(tmp_path):
    scripts = happy_path()
    esc = {"category": "architecture", "severity": "blocker", "title": "greet.sh has no function boundary to extend",
           "detail": "needs redesign", "claims": [{"kind": "file_exists", "statement": "greet.sh exists", "target": "src/greet.sh"}]}
    first = {"n": 0}
    base = scripts[(AgentId.DEV1.value, "dev_writer")]

    def writer(req):
        if req.round == 0 and "Tool results:" not in req.prompt and first["n"] == 0:
            first["n"] += 1
            return {"final": {"summary": "cannot implement as planned", "escalations": [esc]}}
        return base(req)

    scripts[(AgentId.DEV1.value, "dev_writer")] = writer
    r = Run(tmp_path, scripts)
    out = r.start()
    assert r.state["architecture_round"] == 1
    assert r.state["plan_version"] == 2
    assert "escalations_from_implementation" in r.calls(AgentId.MELCHIOR, "proposal")[-1].prompt
    assert {p["meta"]["round"] for p in r.state["proposals"].values()} == {0, 1}
    assert out.status.value == "SUCCESS" and out.plan_version == 2


def test_rounds_do_not_mix(tmp_path):
    from magi_system.offline import _proposal

    def bad_then_good(req):
        out = _proposal(req)
        if req.round == 0:
            out["final"]["coverage"] = []
        return out

    scripts = happy_path()
    for a in (AgentId.MELCHIOR, AgentId.BALTHASAR, AgentId.CASPER):
        scripts[(a.value, "proposal")] = bad_then_good
    r = Run(tmp_path, scripts)
    out = r.start()
    sel0, sel1 = r.state["decisions"]["selection:r0"], r.state["decisions"]["selection:r1"]
    r0 = {p["meta"]["id"] for p in r.state["proposals"].values() if p["meta"]["round"] == 0}
    r1 = {p["meta"]["id"] for p in r.state["proposals"].values() if p["meta"]["round"] == 1}
    assert {s["proposal_id"] for s in sel0["standings"]} == r0
    assert {s["proposal_id"] for s in sel1["standings"]} == r1
    assert not r0 & r1
    assert all(p["version"] == 2 for p in r.state["proposals"].values() if p["meta"]["round"] == 1)
    assert out.status.value == "SUCCESS"


def test_two_concurrent_runs_on_two_projects_are_isolated(tmp_path):
    from magi_system.offline import DEMO_FILES

    other = dict(DEMO_FILES)
    other["README.md"] = "# other project\n"
    r1, r2 = Run(tmp_path / "a"), Run(tmp_path / "b", files=other)
    r2.service = r1.service.__class__(tmp_path / "a" / "runs", models=r2.models)  # same runs_root, different run ids
    results = {}

    def go(name, r):
        results[name] = r.start(run_id=f"iso-{name}")

    threads = [threading.Thread(target=go, args=("one", r1)), threading.Thread(target=go, args=("two", r2))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results["one"].status.value == "SUCCESS" and results["two"].status.value == "SUCCESS"
    ws = r1.service.workspaces
    assert ws.file_at("iso-one", results["one"].final_commit, "README.md") == DEMO_FILES["README.md"]
    assert ws.file_at("iso-two", results["two"].final_commit, "README.md") == "# other project\n"
    assert results["one"].baseline_commit != results["two"].baseline_commit
