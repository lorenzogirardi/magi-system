"""MAGI decision protocol scenarios (brief §14 #1-10, §8 invariants)."""

from magi_system.llm import ModelTimeout
from magi_system.models.common import AgentId
from magi_system.models.run import RunBudget
from magi_system.offline import _proposal, block
from magi_system.service import RunSettings
from tests.integration.harness import Run

M, B, C = AgentId.MELCHIOR.value, AgentId.BALTHASAR.value, AgentId.CASPER.value


def selection(r: Run, round: int = 0) -> dict:
    return r.state["decisions"][f"selection:r{round}"]


def standing(r: Run, author: str, round: int = 0) -> dict:
    pid = next(p["meta"]["id"] for p in r.state["proposals"].values() if p["author"] == author and p["meta"]["round"] == round)
    return next(s for s in selection(r, round)["standings"] if s["proposal_id"] == pid)


def proposal_with(**changes):
    def respond(req):
        out = _proposal(req)
        out["final"].update(changes)
        return out
    return respond


def per_agent(default, **overrides):
    return lambda req: overrides.get(req.agent, default)(req)


def finding_on(label_author: str, finding: dict):
    """Cross-review that raises `finding` against the proposal authored by `label_author`."""
    def respond(req):
        shown = block(req.prompt, "Proposals (author masked)") or {}
        out = []
        for label, p in sorted(shown.items()):
            f = [finding] if p["title"].startswith(TITLES[label_author]) else []
            out.append({"label": label, "scores": [{"criterion": c, "score": 4, "rationale": "ok"} for c in RUBRIC], "supports": not f, "findings": f})
        return {"assessments": out}
    return respond


TITLES = {M: "Extend greet.sh in place", B: "Extend greet.sh with a guarded", C: "New farewell.sh module"}
RUBRIC = ("requirement_fit", "simplicity", "risk", "operability", "testability", "maintainability")


def test_three_valid_proposals_deterministic_selection(tmp_path):
    winners = set()
    for i in range(2):
        r = Run(tmp_path / f"r{i}")
        r.start()
        sel = selection(r)
        assert sel["outcome"] == "selected"
        assert all(s["admissible"] for s in sel["standings"])
        winners.add(r.state["proposals"][sel["selected_proposal_id"]]["author"])
    # Melchior and Balthasar tie (same score and change count): magi_order tie-break → Melchior
    assert winners == {M}
    assert "tie-break" in selection(r)["tie_break_applied"]


def test_reviewers_never_score_own_proposal(tmp_path):
    r = Run(tmp_path)
    r.start()
    props = {p["meta"]["id"]: p["author"] for p in r.state["proposals"].values()}
    for a in r.state["assessments"].values():
        assert props[a["proposal_id"]] != a["reviewer"]
    for call in r.calls(purpose="cross_review"):
        shown = block(call.prompt, "Proposals (author masked)")
        assert len(shown) == 2 and all("author" not in p for p in shown.values())


def test_only_one_admissible_proposal(tmp_path):
    scripts = __import__("magi_system.offline", fromlist=["happy_path"]).happy_path()
    bad = proposal_with(coverage=[])  # covers no mandatory requirement
    for a in (M, B):
        scripts[(a, "proposal")] = bad
    r = Run(tmp_path, scripts)
    out = r.start()
    assert [standing(r, a)["admissible"] for a in (M, B, C)] == [False, False, True]
    assert r.state["proposals"][r.state["selected_plan"]]["author"] == C
    assert out.status.value == "SUCCESS"


def test_no_admissible_proposal_terminates_after_bounded_revision(tmp_path):
    from magi_system.offline import happy_path

    scripts = happy_path()
    for a in (M, B, C):
        scripts[(a, "proposal")] = proposal_with(coverage=[])
    r = Run(tmp_path, scripts, settings=RunSettings(budget=RunBudget(max_architecture_rounds=2)))
    out = r.start()
    assert out.status.value == "UNRESOLVED_REQUIREMENT"
    assert selection(r, 0)["outcome"] == "revise" and selection(r, 1)["outcome"] == "no_admissible"
    assert not r.calls(AgentId.DEV1)  # nothing implemented


def test_confirmed_blocker_beats_two_favourable_votes(tmp_path):
    from magi_system.offline import happy_path

    scripts = happy_path()
    # Security proves (with a checkable claim) that Melchior's plan touches a protected path.
    scripts[(M, "proposal")] = proposal_with(changes=[
        {"path": "src/greet.sh", "action": "modify", "description": "add farewell"},
        {"path": "tests/run.sh", "action": "modify", "description": "test"},
    ], claims=[{"kind": "symbol_exists", "statement": "greet exists", "target": "src/greet.sh::greet", "critical": True}])
    blocker = {"category": "security", "severity": "blocker", "title": "plan ignores existing greet contract",
               "detail": "greet must stay", "claims": [{"kind": "file_exists", "statement": "README exists", "target": "README.md"}]}
    scripts[(C, "cross_review")] = finding_on(M, blocker)
    r = Run(tmp_path, scripts)
    r.start()
    m = standing(r, M)
    assert not m["admissible"] and any("confirmed blocker" in x for x in m["reasons"])
    # the other two MAGI supported it; majority does not override the confirmed blocker
    supports = [a["supports"] for a in r.state["assessments"].values() if a["proposal_id"] == m["proposal_id"]]
    assert supports.count(True) == 1 and supports.count(False) == 1
    assert r.state["proposals"][r.state["selected_plan"]]["author"] != M


def test_invented_objection_is_refuted_and_not_a_veto(tmp_path):
    from magi_system.offline import happy_path

    scripts = happy_path()
    invented = {"category": "design", "severity": "blocker", "title": "greet.sh does not exist",
                "detail": "the file is missing", "claims": [{"kind": "file_exists", "statement": "src/greet.sh is missing",
                                                             "target": "src/greet.sh", "expect": False}]}
    scripts[(C, "cross_review")] = finding_on(M, invented)
    r = Run(tmp_path, scripts)
    r.start()
    f = next(f for f in r.state["findings"].values() if f["title"] == "greet.sh does not exist")
    assert f["status"] == "REFUTED"
    assert standing(r, M)["admissible"]


def test_objection_without_evidence_is_unresolved_not_a_veto(tmp_path):
    from magi_system.offline import happy_path

    scripts = happy_path()
    scripts[(C, "cross_review")] = finding_on(M, {"category": "design", "severity": "blocker", "title": "feels risky", "detail": "no evidence"})
    r = Run(tmp_path, scripts)
    r.start()
    f = next(f for f in r.state["findings"].values() if f["title"] == "feels risky")
    assert f["status"] == "UNRESOLVED"
    assert standing(r, M)["admissible"]


def test_nonexistent_symbol_makes_proposal_inadmissible(tmp_path):
    from magi_system.offline import happy_path

    scripts = happy_path()
    scripts[(M, "proposal")] = proposal_with(claims=[
        {"kind": "symbol_exists", "statement": "reuse the existing say_goodbye helper", "target": "src/greet.sh::say_goodbye", "critical": True}])
    r = Run(tmp_path, scripts)
    r.start()
    m = standing(r, M)
    assert not m["admissible"] and any("critical claim refuted" in x for x in m["reasons"])
    claim = next(c for c in r.state["claims"].values() if c["target"] == "src/greet.sh::say_goodbye")
    assert claim["status"] == "refuted"


def test_capability_missing_in_version_and_nonexistent_policy(tmp_path):
    from magi_system.offline import happy_path

    scripts = happy_path()
    scripts[(M, "proposal")] = proposal_with(claims=[
        {"kind": "package_version", "statement": "bats >= 1.10 is available", "target": "bats>=1.10", "critical": True}])
    scripts[(B, "proposal")] = proposal_with(claims=[
        {"kind": "policy_exists", "statement": "policy allows auto deploy", "target": "deployment.auto_approve", "critical": True}])
    r = Run(tmp_path, scripts)
    r.start()
    assert not standing(r, M)["admissible"] and not standing(r, B)["admissible"]
    assert r.state["proposals"][r.state["selected_plan"]]["author"] == C


def test_unresolved_critical_unknown_blocks(tmp_path):
    from magi_system.offline import happy_path

    scripts = happy_path()
    unknown = proposal_with(assumptions=[{"text": "business wants the farewell localized", "critical": True}],
                            claims=[{"kind": "business_assumption", "statement": "localization required", "target": "product", "critical": True}])
    for a in (M, B, C):
        scripts[(a, "proposal")] = unknown
    r = Run(tmp_path, scripts, settings=RunSettings(budget=RunBudget(max_architecture_rounds=1)))
    out = r.start()
    assert out.status.value == "UNRESOLVED_REQUIREMENT"
    reasons = [x for s in selection(r)["standings"] for x in s["reasons"]]
    assert any("critical unknown unresolved" in x for x in reasons)
    assert any("critical claim unresolved" in x for x in reasons)


def test_common_mode_error_caught_by_external_check(tmp_path):
    """All three MAGI wrongly rely on a nonexistent symbol; tool verification catches it."""
    from magi_system.offline import happy_path

    scripts = happy_path()
    wrong = proposal_with(claims=[{"kind": "symbol_exists", "statement": "greet_all exists", "target": "src/greet.sh::greet_all", "critical": True}])
    for a in (M, B, C):
        scripts[(a, "proposal")] = wrong
    r = Run(tmp_path, scripts, settings=RunSettings(budget=RunBudget(max_architecture_rounds=1)))
    out = r.start()
    assert all(a["supports"] for a in r.state["assessments"].values())  # unanimous support...
    assert out.status.value == "FAILED_VERIFICATION"  # ...does not replace verification
    assert not r.calls(AgentId.DEV1)


def test_magi_timeout_and_invalid_output_are_typed_failures(tmp_path):
    from magi_system.offline import _analysis, happy_path

    scripts = happy_path()
    scripts[(B, "analysis")] = [ModelTimeout("simulated"), {"final": _analysis(None)["final"]}]
    scripts[(C, "analysis")] = ["this is not json", "still not json", {"final": _analysis(None)["final"]}]
    r = Run(tmp_path, scripts)
    out = r.start()
    fails = [e["failure"] for e in r.events("agent_failure")]
    assert "timeout" in fails and "invalid_output" in fails
    assert r.events("technical_retry")
    assert out.status.value == "SUCCESS"  # retried within the technical retry budget


def test_persistent_magi_failure_stops_without_partial_selection(tmp_path):
    from magi_system.offline import happy_path

    scripts = happy_path()
    scripts[(C, "analysis")] = [ModelTimeout("simulated")]
    r = Run(tmp_path, scripts)
    out = r.start()
    assert out.status.value == "INFRA_FAILURE"
    assert "only 2 of 3" in out.diagnosis
    assert not r.state.get("proposals")


def test_quorum_profile_allows_partial_results_explicitly(tmp_path):
    from magi_system.offline import happy_path

    scripts = happy_path()
    scripts[(C, "analysis")] = [ModelTimeout("simulated")]
    r = Run(tmp_path, scripts, profile="offline-test-quorum-2")
    out = r.start()
    assert r.events("quorum_proceed")
    assert {p["author"] for p in r.state["proposals"].values()} == {M, B, C}  # Casper still proposes
    assert out.status.value == "SUCCESS"


def test_budget_exhaustion(tmp_path):
    r = Run(tmp_path, settings=RunSettings(budget=RunBudget(max_llm_calls=5)))
    out = r.start()
    assert out.status.value == "BUDGET_EXHAUSTED"
    assert len(r.model.calls) <= 5
    assert r.events("budget_exhausted")


def test_derived_requirement_never_becomes_mandatory(tmp_path):
    from magi_system.offline import DEMO_QUOTE, happy_path

    def analysis(req):
        return {"final": {"understanding": "x", "requirements": [
            {"text": "farewell", "kind": "explicit", "source_quote": DEMO_QUOTE},
            {"text": "must also support French", "kind": "derived", "derived_from_quotes": ["not in the request"]},
            {"text": "farewell must be POSIX sh", "kind": "derived", "derived_from_quotes": ["src/greet.sh"]},
        ]}}

    scripts = happy_path()
    for a in (M, B, C):
        scripts[(a, "analysis")] = analysis
    r = Run(tmp_path, scripts)
    r.start()
    reqs = list(r.state["requirements"].values())
    assert [x["text"] for x in reqs if x["mandatory"]] == ["farewell"]
    assert not any("French" in x["text"] for x in reqs)
    assert any(e["text"] == "must also support French" for e in r.events("requirement_rejected"))
