"""End-to-end: request → MAGI → writer → pair → verification → documentation → gate,
on a temporary target repository distinct from magi-system (shell/Makefile stack)."""

from magi_system.models.common import AgentId
from magi_system.workspace import git
from tests.conftest import control_digest, tree_digest
from tests.integration.harness import Run

ORDER = ["intake", "magi", "dev.writer", "dev.pair", "verify", "docs", "tech_writer", "verify", "final_review", "gate", "gate"]  # triage→gate, gate→end


def test_end_to_end_success(tmp_path):
    control_before = control_digest()
    r = Run(tmp_path)
    source_before = tree_digest(r.repo)
    out = r.start()
    assert out.status.value == "SUCCESS", out.diagnosis
    assert set(r.gates().values()) <= {"PASS", "NOT_APPLICABLE"}
    assert r.gates()["sre.review"] == "NOT_APPLICABLE"  # justified and allowed by policy
    # the change is a verified candidate, not integrated or deployed
    assert out.change_level.value == "verified"
    assert {p.operation for p in out.publications} == {"export_patch", "local_branch"}
    patch = (r.service.workspaces.run_dir(r.run_id) / out.patch_ref).read_text()
    assert "farewell()" in patch and "farewell World" in patch and "docs/usage.md" in patch
    # all gate evidence is bound to the final candidate
    assert {c.candidate_commit for c in out.gate_checks} == {out.final_commit}
    # neither the caller's checkout nor the control repository changed
    assert tree_digest(r.repo) == source_before
    assert control_digest() == control_before
    assert git("log", "--oneline", cwd=r.repo).stdout.count("\n") == 1
    # phase order
    trail = [x.get("to") if x.get("from") == "triage" else x.get("from") for x in r.state["routing"]]
    assert [t for t in trail if t in ORDER] == ORDER
    report = (r.service.workspaces.artifacts_path(r.run_id) / "report.md").read_text()
    assert "Integrated (merged): no" in report and "No push, pull request, merge or deployment was performed." in report


def test_dev1_then_dev2_never_in_parallel(tmp_path):
    r = Run(tmp_path)
    r.start()
    starts = [e["node"] for e in r.events("node_start")]
    i_w, i_c, i_a, i_p = (starts.index(n) for n in ("dev_writer", "dev_initial_checks", "dev_pair_assess", "dev_pair"))
    assert i_w < i_c < i_a < i_p
    ends = [e for e in r.events() if e["kind"] in ("node_start", "node_end") and e["node"] in ("dev_writer", "dev_pair")]
    assert [(e["kind"], e["node"]) for e in ends] == [("node_start", "dev_writer"), ("node_end", "dev_writer"),
                                                       ("node_start", "dev_pair"), ("node_end", "dev_pair")]
    # pair's first look happens before it is shown DEV1's handoff
    assessment = r.calls(AgentId.DEV2, "pair_assessment")[0]
    assert "DEV1 handoff" not in assessment.prompt
    assert "## DEV1 handoff" in r.calls(AgentId.DEV2, "dev_pair")[0].prompt


def test_pair_without_changes(tmp_path):
    r = Run(tmp_path)
    r.start()
    pair = [h for h in r.state["task_results"].values() if h.get("kind") == "handoff" and h["author"] == "dev.pair"]
    assert pair and pair[0]["status"] == "no_changes"
    cs = [h for h in r.state["task_results"].values() if h.get("kind") == "changeset" and h["author"] == "dev.pair"]
    assert cs[0]["empty"] is True
    assert r.outcome.status.value == "SUCCESS"
