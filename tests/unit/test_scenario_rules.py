"""A QA scenario that does not test behaviour must not decide the run (real commands from runs 092321 and 101618)."""

import pytest

from magi_system.engine.gates import is_source_text_scan, scenario_verdict
from magi_system.engine.nodes_verify import acceptance_verdict
from magi_system.models.common import Verdict

# Commands the QA agent really wrote, copied from the events of the AgentCore runs.
AS06 = ("python -c \"import pathlib,sys; src=''.join(p.read_text() for p in pathlib.Path('app/toolbox').rglob('*.py')); "
        "bad=[m for m in ('httpx','requests','socket','subprocess','kubernetes','urllib') if m in src]; print(bad); sys.exit(1 if bad else 0)\"")
NO_NET = ("python -c \"import pathlib,re,sys; bad=[str(p) for p in pathlib.Path('app').rglob('*.py') if 'toolbox' in str(p) "
          "and re.search(r'\\b(subprocess|socket|httpx|importlib|eval|exec)\\b', p.read_text())]; print(bad); sys.exit(1 if bad else 0)\"")
BEHAVIOUR = [
    "python -m pytest -q",
    'python -m pytest tests/ -q -k "toolbox and k8s"',
    "python -c \"from app.main import create_app; create_app()\"",
    "python -c \"from app.main import create_app; s=create_app().openapi(); assert '/api/toolbox/tools' in s['paths']\"",
    "python -m flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics",
]
AST_CHECK = ("python -c \"import ast,pathlib; bad=[p for p in pathlib.Path('app').rglob('*.py') "
             "if any(isinstance(n, ast.Import) and n.names[0].name=='socket' for n in ast.walk(ast.parse(p.read_text())))]; "
             "raise SystemExit(1 if bad else 0)\"")


@pytest.mark.parametrize("command", [AS06, NO_NET, "grep -rn requests app/toolbox", "cd app && rg -n 'exec' ."])
def test_source_text_searches_are_recognised(command):
    assert is_source_text_scan(command)


@pytest.mark.parametrize("command", [*BEHAVIOUR, AST_CHECK])
def test_behaviour_and_structure_checks_are_not_text_searches(command):
    assert not is_source_text_scan(command)


def test_a_failing_text_search_is_advisory_and_keeps_the_matches():
    verdict, note = scenario_verdict(NO_NET, Verdict.FAIL, 1, "['app/services/toolbox/base.py']\n")
    assert verdict is Verdict.NOT_APPLICABLE and "base.py" in note and "TEXT" in note


def test_pytest_that_selects_no_tests_verifies_nothing():
    verdict, note = scenario_verdict('python -m pytest tests/ -q -k "toolbox and determinism"', Verdict.FAIL, 5, "")
    assert verdict is Verdict.NOT_APPLICABLE and "no tests" in note


def test_real_failures_and_passes_are_untouched():
    for command in BEHAVIOUR:
        assert scenario_verdict(command, Verdict.FAIL, 1, "assert failed") == (Verdict.FAIL, "")
        assert scenario_verdict(command, Verdict.PASS, 0, "") == (Verdict.PASS, "")
    assert scenario_verdict("python -m pytest -q", Verdict.FAIL, 2, "") == (Verdict.FAIL, "")  # exit 2 is not "no tests"
    assert scenario_verdict(AS06, Verdict.ERROR, None, "") == (Verdict.ERROR, "")  # only a plain FAIL is reinterpreted


def test_acceptance_ignores_advisory_scenarios_but_needs_one_pass():
    checks = {"qa.scenario:a": "PASS", "qa.scenario:b": "NOT_APPLICABLE"}
    verdict, detail = acceptance_verdict(checks, [])
    assert verdict is Verdict.PASS and "advisory" in detail and "qa.scenario:b" in detail

    assert acceptance_verdict({"qa.scenario:a": "PASS", "qa.scenario:b": "FAIL"}, [])[0] is Verdict.FAIL  # a real failure still blocks
    assert acceptance_verdict({"qa.scenario:a": "PASS", "qa.scenario:b": "ERROR"}, [])[0] is Verdict.FAIL
    assert acceptance_verdict({"qa.scenario:a": "NOT_APPLICABLE"}, [])[0] is Verdict.UNKNOWN  # nothing demonstrated the change
    assert acceptance_verdict({}, [])[0] is Verdict.UNKNOWN
    assert acceptance_verdict({"qa.scenario:a": "PASS"}, ["blocker"])[0] is Verdict.FAIL  # confirmed QA findings still block


def test_a_malformed_claim_is_rejected_where_the_agent_can_fix_it():
    """Runs 103712 and 111040 died because TRUE claims in the wrong format ('app/auth.py' instead of
    'app/auth.py::verify_credentials'; 'platform policy allowed_commands' instead of a dotted key) were "refuted" by the
    verifier. The mistake is now an invalid agent output: AgentCaller retries with the message, no crash, no false fact."""
    import json

    from pydantic import ValidationError

    from magi_system.engine.helpers import claims_from_drafts
    from magi_system.engine.schemas import AnalysisDraft, ClaimDraft
    from magi_system.llm.base import InvalidStructuredOutput, parse_structured
    from magi_system.models.knowledge import ClaimKind

    def draft(kind, target):
        return ClaimDraft(kind=kind, statement="s", target=target)

    draft(ClaimKind.SYMBOL_EXISTS, "app/auth.py::verify_credentials")
    draft(ClaimKind.SYMBOL_EXISTS, "*::verify_credentials")
    draft(ClaimKind.POLICY_EXISTS, "limits.max_tool_calls")
    draft(ClaimKind.FILE_EXISTS, "app/auth.py")  # other kinds are unaffected
    for kind, target in ((ClaimKind.SYMBOL_EXISTS, "app/auth.py"), (ClaimKind.POLICY_EXISTS, "platform policy allowed_commands")):
        with pytest.raises(ValidationError):
            draft(kind, target)

    # through the real parsing path: an InvalidStructuredOutput is what makes the caller retry
    bad = {"summary": "s", "requirements": [], "claims": [{"kind": "symbol_exists", "statement": "s", "target": "app/auth.py"}]}
    with pytest.raises(InvalidStructuredOutput, match="path::symbol"):
        parse_structured(json.dumps(bad), AnalysisDraft)
    # and a valid draft still turns into claims without error
    assert claims_from_drafts([draft(ClaimKind.SYMBOL_EXISTS, "app/auth.py::verify_credentials")], "o")[0].target.endswith("::verify_credentials")


# -- the QA may correct a scenario that FAILED once it has seen the code (the verdict stays that of an execution) --

def _scenario(name, command, **extra):
    return {"id": f"acc-{name}", "round": 0, "name": name, "command": command, "denied": False, **extra}


def _rewrite(name, command):
    from magi_system.engine.schemas import AcceptanceScenarioDraft

    return AcceptanceScenarioDraft(id=name, description="the scenario searched text", command=command)


def test_qa_can_correct_a_failed_scenario():
    from magi_system.engine.nodes_verify import pick_scenario_rewrites

    scenarios = {"a": _scenario("no-net", NO_NET)}
    picked = pick_scenario_rewrites(scenarios, {"qa.scenario:no-net": "FAIL"}, [_rewrite("no-net", AST_CHECK)], lambda c: True)
    assert [(old["name"], new.command) for old, new in picked] == [("no-net", AST_CHECK)]


@pytest.mark.parametrize("why,executed,rewrites,allowed", [
    ("it did not fail", {"qa.scenario:no-net": "PASS"}, [("no-net", AST_CHECK)], True),
    ("it was advisory, not failed", {"qa.scenario:no-net": "NOT_APPLICABLE"}, [("no-net", AST_CHECK)], True),
    ("unknown scenario id", {"qa.scenario:no-net": "FAIL"}, [("other", AST_CHECK)], True),
    ("same command, nothing corrected", {"qa.scenario:no-net": "FAIL"}, [("no-net", NO_NET)], True),
    ("empty command", {"qa.scenario:no-net": "FAIL"}, [("no-net", "")], True),
    ("command not allowed by policy", {"qa.scenario:no-net": "FAIL"}, [("no-net", AST_CHECK)], False),
])
def test_qa_corrections_that_are_refused(why, executed, rewrites, allowed):
    from magi_system.engine.nodes_verify import pick_scenario_rewrites

    scenarios = {"a": _scenario("no-net", NO_NET)}
    assert pick_scenario_rewrites(scenarios, executed, [_rewrite(*r) for r in rewrites], lambda c: allowed) == [], why


def test_a_scenario_is_corrected_once_only():
    from magi_system.engine.nodes_verify import pick_scenario_rewrites

    scenarios = {"old": _scenario("no-net", NO_NET, superseded=True),
                 "new": _scenario("no-net", AST_CHECK, amended_from="old")}
    again = pick_scenario_rewrites(scenarios, {"qa.scenario:no-net": "FAIL"}, [_rewrite("no-net", BEHAVIOUR[1])], lambda c: True)
    assert again == []  # the corrected one failed too: it is a real failure now, no endless loop


def test_a_bare_path_symbol_claim_is_completed_from_its_pattern():
    """In runs 103712, 111040 and 144624 the models wrote {'target': 'app/auth.py', 'pattern': 'def verify_credentials'}."""
    from pydantic import ValidationError

    from magi_system.engine.schemas import ClaimDraft

    d = ClaimDraft.model_validate({"kind": "symbol_exists", "statement": "s", "target": "app/auth.py", "pattern": "def verify_credentials"})
    assert d.target == "app/auth.py::verify_credentials"
    assert ClaimDraft.model_validate({"kind": "symbol_exists", "statement": "s", "target": "a.py", "pattern": "async def run"}).target == "a.py::run"
    assert ClaimDraft.model_validate({"kind": "symbol_exists", "statement": "s", "target": "a.py", "pattern": "class Foo(Base):"}).target == "a.py::Foo"
    assert ClaimDraft.model_validate({"kind": "symbol_exists", "statement": "s", "target": "a.py::keep", "pattern": "def other"}).target == "a.py::keep"
    with pytest.raises(ValidationError):  # nothing to complete it with: still reported to the agent
        ClaimDraft.model_validate({"kind": "symbol_exists", "statement": "s", "target": "a.py", "pattern": "somewhere in the file"})
