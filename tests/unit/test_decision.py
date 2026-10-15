"""Pure decision logic and tool-based claim verification (brief §8)."""

from pathlib import Path

import pytest

from magi_system.decision.selection import admissibility, rank, resolve_finding_status, score_proposal
from magi_system.decision.verifier import ClaimVerifier
from magi_system.execution import LocalRunner
from magi_system.models import (
    AgentId,
    ArchitectureProposal,
    ArtifactMeta,
    Assumption,
    Claim,
    ClaimKind,
    ClaimStatus,
    CriterionScore,
    Finding,
    FindingCategory,
    FindingStatus,
    Phase,
    PlannedChange,
    ProposalAssessment,
    Requirement,
    RequirementCoverage,
    RequirementKind,
    Severity,
)
from magi_system.policy import PolicyEngine

RUBRIC = {"fit": 2, "simplicity": 1}


def m(id, producer=AgentId.PLATFORM):
    return ArtifactMeta(id=id, producer=producer, round=0, phase=Phase.SELECTION, input_hash="h")


REQ = Requirement(meta=m("req-1"), kind=RequirementKind.EXPLICIT, text="x", source_quote="x")


def prop(id, author, *, changes=1, claims=(), assumptions=(), covered=True):
    return ArchitectureProposal(
        meta=m(id, author), author=author, title=id, summary="s",
        coverage=(RequirementCoverage(requirement_id="req-1", covered=covered, how="h"),),
        claims=tuple(claims), assumptions=tuple(assumptions),
        changes=tuple(PlannedChange(path=f"f{i}", action="modify", description="d") for i in range(changes)),
        verification_plan=("make test",), migration_rollback="revert",
    )


def assess(reviewer, pid, fit, simplicity=3, supports=True):
    return ProposalAssessment(meta=m(f"a-{reviewer.value}-{pid}", reviewer), reviewer=reviewer, proposal_id=pid, masked_label="L",
                              scores=(CriterionScore(criterion="fit", score=fit, rationale="r"),
                                      CriterionScore(criterion="simplicity", score=simplicity, rationale="r")), supports=supports)


def claim(id, status, critical=True):
    return Claim(id=id, kind=ClaimKind.FILE_EXISTS, statement=id, target="x", critical=critical, status=status)


def blocker(subject, status, claims=()):
    return Finding(meta=m(f"f-{subject}", AgentId.CASPER), subject_id=subject, category=FindingCategory.DESIGN,
                   severity=Severity.BLOCKER, title="t", detail="d", claims=tuple(claims), status=status)


def test_finding_status():
    sup, ref, unv = claim("c1", ClaimStatus.SUPPORTED), claim("c2", ClaimStatus.REFUTED), claim("c3", ClaimStatus.UNVERIFIABLE)
    assert resolve_finding_status(blocker("p", FindingStatus.OPEN), {})[0] is FindingStatus.UNRESOLVED
    assert resolve_finding_status(blocker("p", FindingStatus.OPEN, [sup]), {})[0] is FindingStatus.CONFIRMED
    assert resolve_finding_status(blocker("p", FindingStatus.OPEN, [sup, ref]), {})[0] is FindingStatus.REFUTED
    assert resolve_finding_status(blocker("p", FindingStatus.OPEN, [sup, unv]), {})[0] is FindingStatus.UNRESOLVED


def test_only_confirmed_blockers_block():
    p = prop("p", AgentId.MELCHIOR)
    for status in (FindingStatus.OPEN, FindingStatus.UNRESOLVED, FindingStatus.REFUTED):
        assert admissibility(p, requirements=[REQ], findings=[blocker("p", status)], claim_status={})[0]
    assert not admissibility(p, requirements=[REQ], findings=[blocker("p", FindingStatus.CONFIRMED)], claim_status={})[0]


def test_unknown_is_not_pass():
    p = prop("p", AgentId.MELCHIOR, claims=[claim("c", ClaimStatus.UNVERIFIABLE)])
    ok, reasons = admissibility(p, requirements=[REQ], findings=[], claim_status={})
    assert not ok and "critical claim unresolved" in reasons[0]
    p2 = prop("p", AgentId.MELCHIOR, assumptions=[Assumption(id="a", text="?", critical=True)])
    assert not admissibility(p2, requirements=[REQ], findings=[], claim_status={})[0]


def test_self_scores_are_ignored():
    p = prop("p", AgentId.MELCHIOR)
    assert score_proposal(p, [assess(AgentId.MELCHIOR, "p", 5)], RUBRIC) is None
    assert score_proposal(p, [assess(AgentId.MELCHIOR, "p", 5), assess(AgentId.CASPER, "p", 2)], RUBRIC) == pytest.approx((2 * 2 + 3) / 3, abs=1e-3)


def test_no_compensation_and_ranking():
    a, b, c = prop("a", AgentId.MELCHIOR), prop("b", AgentId.BALTHASAR), prop("c", AgentId.CASPER, covered=False)
    assessments = [assess(AgentId.CASPER, "a", 3), assess(AgentId.CASPER, "b", 4), assess(AgentId.MELCHIOR, "c", 5),
                   assess(AgentId.BALTHASAR, "a", 3), assess(AgentId.MELCHIOR, "b", 4), assess(AgentId.BALTHASAR, "c", 5)]
    outcome, winner, standings, tie = rank([a, b, c], requirements=[REQ], findings=[], assessments=assessments, claim_status={},
                                           rubric=RUBRIC, tie_break=["magi_order"], tie_epsilon=0.01)
    assert winner == "b" and not tie
    by = {s.proposal_id: s for s in standings}
    assert not by["c"].admissible and by["c"].score is None and by["c"].rank is None  # top scores, still excluded


def test_tie_break_is_deterministic():
    a, b = prop("a", AgentId.BALTHASAR, changes=1), prop("b", AgentId.MELCHIOR, changes=2)
    assessments = [assess(AgentId.CASPER, "a", 4), assess(AgentId.CASPER, "b", 4)]
    for rules, expected in ((["fewer_planned_changes"], "a"), (["magi_order"], "b")):
        _, winner, _, tie = rank([b, a], requirements=[REQ], findings=[], assessments=assessments, claim_status={},
                                 rubric=RUBRIC, tie_break=rules, tie_epsilon=0.05)
        assert winner == expected and tie


@pytest.fixture
def verifier(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.sh").write_text("greet() {\n  echo hi\n}\n")
    (tmp_path / "package.json").write_text('{"dependencies": {"left-pad": "^1.3.0"}}')
    (tmp_path / "Makefile").write_text("test:\n\ttrue\n")
    (tmp_path / "requirements.txt").write_text("# deps\nfastapi==0.143.1\nuvicorn[standard]==0.54.0  # server\n")
    policy = PolicyEngine("offline-test")
    return ClaimVerifier(tmp_path, policy, LocalRunner(policy, tmp_path), "c0ffee", 0)


@pytest.mark.parametrize(
    ("kind", "target", "pattern", "expect", "status"),
    [
        ("file_exists", "src/a.sh", "", True, "supported"),
        ("file_exists", "src/nope.sh", "", True, "refuted"),
        ("file_exists", "../etc/passwd", "", True, "refuted"),
        ("symbol_exists", "src/a.sh::greet", "", True, "supported"),
        ("symbol_exists", "*::greet", "", True, "supported"),
        ("symbol_exists", "src/a.sh::invented_api", "", True, "refuted"),
        ("symbol_exists", "src/a.sh::invented_api", "", False, "supported"),
        ("text_present", "src/a.sh", r"echo\s+hi", True, "supported"),
        ("text_present", "src/a.sh", "greet() {", True, "supported"),  # literal with regex metachars
        ("text_present", "src/a.sh", "greet(", True, "supported"),  # not a valid regex, matched literally
        ("text_present", "src/a.sh", "farewell(", True, "refuted"),
        ("package_version", "left-pad>=1.0", "", True, "supported"),
        ("package_version", "left-pad>=2.0", "", True, "refuted"),
        ("package_version", "react", "", True, "refuted"),
        ("package_version", "fastapi>=0.100", "", True, "supported"),
        ("package_version", "requirements.txt::uvicorn", "", True, "supported"),
        ("package_version", "fastapi>=1.0", "", True, "refuted"),
        ("package_version", "fastapi==0.143.1", "", True, "supported"),
        ("package_version", "fastapi==0.1.0", "", True, "refuted"),
        ("package_version", "requirements.txt", "uvicorn[standard]==0.54.0", True, "supported"),
        ("package_version", "requirements.txt", "fastapi==9.9.9", True, "refuted"),
        ("policy_exists", "limits.command_timeout_seconds", "", True, "supported"),
        ("policy_exists", "deployment.auto_approve", "", True, "refuted"),
        ("command_succeeds", "make test", "", True, "supported"),
        ("command_succeeds", "curl http://x", "", True, "unverifiable"),
        ("operational_guarantee", "99.99% uptime", "", True, "unverifiable"),
        ("business_assumption", "users want it", "", True, "unverifiable"),
    ],
)
def test_verifier(verifier, kind, target, pattern, expect, status):
    c = Claim(id="c", kind=ClaimKind(kind), statement="s", target=target, pattern=pattern, expect=expect)
    new, result = verifier.verify(c)
    assert new.status.value == status, result.detail
    assert result.candidate_commit == "c0ffee" and new.evidence_refs == (result.meta.id,)
