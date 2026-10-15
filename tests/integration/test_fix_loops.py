"""Implementation, pairing and correction loops (brief §14 #12-14, #18, #20; §9)."""

from magi_system.models.common import AgentId
from magi_system.models.run import RunBudget
from magi_system.offline import DEMO_FILES, FAREWELL, FAREWELL_TEST, happy_path, tool_turn
from magi_system.service import RunSettings
from tests.integration.harness import Run

GOOD_GREET = DEMO_FILES["src/greet.sh"] + FAREWELL
GOOD_TESTS = DEMO_FILES["tests/run.sh"] + FAREWELL_TEST
BROKEN_GREET = DEMO_FILES["src/greet.sh"] + 'farewell() {\n  echo "Bye, $1"\n}\n'


def writes(*pairs):
    return [{"tool": "write_file", "args": {"path": p, "content": c}} for p, c in pairs]


def sequential(*responders):
    """Successive invocations of the same agent step use successive responders."""
    state = {"i": -1, "key": None}

    def respond(req):
        key = (req.round, "Tool results:" in req.prompt)
        if not key[1]:
            state["i"] += 1
        return responders[min(state["i"], len(responders) - 1)](req)
    return respond


def test_pair_introduced_bug_caught_by_gates_and_fixed_by_pair(tmp_path):
    scripts = happy_path()
    scripts[(AgentId.DEV2.value, "dev_pair")] = sequential(
        tool_turn(writes(("src/greet.sh", BROKEN_GREET)), {"summary": "refactored farewell"}),
        tool_turn(writes(("src/greet.sh", GOOD_GREET)), {"summary": "restored correct output"}),
    )
    r = Run(tmp_path, scripts)
    out = r.start()
    rem = [e for e in r.events("remediation")]
    assert rem and rem[0]["target"] == "dev.pair"  # the bug was introduced by the pair
    # evidence for the broken candidate stays bound to that commit; final gates are on a new commit
    failing = [g for g in r.state["gate_results"].values() if g["check"] == "project.test" and g["verdict"] == "FAIL"]
    assert failing and failing[0]["candidate_commit"] != out.final_commit
    assert out.status.value == "SUCCESS"
    assert {c.candidate_commit for c in out.gate_checks} == {out.final_commit}


def test_writer_bug_goes_back_to_writer_then_pair_again(tmp_path):
    scripts = happy_path()
    scripts[(AgentId.DEV1.value, "dev_writer")] = sequential(
        tool_turn(writes(("src/greet.sh", BROKEN_GREET), ("tests/run.sh", GOOD_TESTS)), {"summary": "first try"}),
        tool_turn(writes(("src/greet.sh", GOOD_GREET), ("tests/run.sh", GOOD_TESTS)), {"summary": "fixed output"}),
    )
    r = Run(tmp_path, scripts)
    out = r.start()
    assert [e["target"] for e in r.events("remediation")] == ["dev.writer"]
    assert len(r.calls(AgentId.DEV2, "pair_assessment")) == 2  # pair reviews the corrected candidate too
    assert "Findings you must fix" in r.calls(AgentId.DEV1, "dev_writer")[-1].prompt
    assert out.status.value == "SUCCESS"


def test_fix_invalidates_previous_evidence(tmp_path):
    scripts = happy_path()
    scripts[(AgentId.DEV1.value, "dev_writer")] = sequential(
        tool_turn(writes(("src/greet.sh", BROKEN_GREET), ("tests/run.sh", GOOD_TESTS)), {"summary": "first"}),
        tool_turn(writes(("src/greet.sh", GOOD_GREET), ("tests/run.sh", GOOD_TESTS)), {"summary": "fixed"}),
    )
    r = Run(tmp_path, scripts)
    out = r.start()
    commits = {g["candidate_commit"] for g in r.state["gate_results"].values()}
    assert len(commits) >= 3  # broken candidate, fixed candidate, documented candidate
    # the final gate reads only results for the final commit; stale PASS/FAIL are never reused
    for c in out.gate_checks:
        assert c.candidate_commit == out.final_commit


def test_fix_attempts_are_bounded(tmp_path):
    scripts = happy_path()
    scripts[(AgentId.DEV1.value, "dev_writer")] = tool_turn(writes(("src/greet.sh", BROKEN_GREET), ("tests/run.sh", GOOD_TESTS)), {"summary": "x"})
    r = Run(tmp_path, scripts, settings=RunSettings(budget=RunBudget(max_fixes_per_finding=1)))
    out = r.start()
    assert out.status.value in ("FAILED_VERIFICATION", "BUDGET_EXHAUSTED")
    assert len(r.calls(AgentId.DEV1, "dev_writer")) <= 4  # 2 attempts × (tools turn + final turn)
    assert out.final_commit == ""


def test_agents_cannot_delete_tests_weaken_gates_or_touch_protected_files(tmp_path):
    scripts = happy_path()
    attack = [
        {"tool": "delete_file", "args": {"path": "tests/run.sh"}},
        {"tool": "write_file", "args": {"path": ".magi/config.yaml", "content": "commands: {}\n"}},
        {"tool": "write_file", "args": {"path": "../outside.txt", "content": "x"}},
        {"tool": "write_file", "args": {"path": "Makefile", "content": "test:\n\ttrue\n"}},  # not in the plan
        *writes(("src/greet.sh", GOOD_GREET), ("tests/run.sh", GOOD_TESTS)),
    ]
    scripts[(AgentId.DEV1.value, "dev_writer")] = tool_turn(attack, {"summary": "done"})
    r = Run(tmp_path, scripts)
    out = r.start()
    ws = r.service.workspaces
    makefile = ws.file_at(r.run_id, out.final_commit, "Makefile")
    assert makefile == DEMO_FILES["Makefile"]
    assert ws.file_at(r.run_id, out.final_commit, ".magi/config.yaml") is None
    assert ws.file_at(r.run_id, out.final_commit, "tests/run.sh") == GOOD_TESTS
    assert not (tmp_path / "runs" / r.run_id / "outside.txt").exists()
    failed_tools = [e for e in r.events("tool_call") if e["agent"] == "dev.writer" and not e["ok"]]
    # delete test, write .magi, escape workspace, write a file outside the planned changes
    assert [e["args"]["path"] for e in failed_tools] == ["tests/run.sh", ".magi/config.yaml", "../outside.txt", "Makefile"]
    assert out.status.value == "SUCCESS"


def test_commands_run_by_agents_cannot_modify_protected_files(tmp_path):
    scripts = happy_path()
    scripts[(AgentId.DEV1.value, "dev_writer")] = tool_turn(
        [*writes(("src/greet.sh", GOOD_GREET), ("tests/run.sh", GOOD_TESTS)), {"tool": "run_command", "args": {"command": "sh -c 'mkdir -p .magi; echo x > .magi/config.yaml'"}}],
        {"summary": "done"},
    )
    r = Run(tmp_path, scripts)
    out = r.start()
    assert r.service.workspaces.file_at(r.run_id, out.final_commit, ".magi/config.yaml") is None
    assert any(e["violation"] == "out_of_scope_write_reverted" for e in r.events("policy_violation"))


def test_no_success_when_a_mandatory_check_cannot_run(tmp_path):
    files = dict(DEMO_FILES)
    files["Makefile"] = "build:\n\tsh -n src/greet.sh\n\ntest:\n\tsh tests/run.sh\n"  # no lint target
    r = Run(tmp_path, files=files)
    out = r.start()
    assert r.gates()["project.lint"] == "UNKNOWN"
    assert out.status.value == "FAILED_VERIFICATION"
    assert "project.lint=UNKNOWN" in out.diagnosis


def test_not_applicable_requires_policy_and_justification(tmp_path):
    files = dict(DEMO_FILES)
    files["Makefile"] = "build:\n\tsh -n src/greet.sh\n\ntest:\n\tsh tests/run.sh\n"
    files[".magi/config.yaml"] = "not_applicable:\n  project.lint: no linter for POSIX sh in this project\n  project.test: skip it\n"
    r = Run(tmp_path, files=files)
    out = r.start()
    assert r.gates()["project.lint"] == "NOT_APPLICABLE"  # allowed by policy and justified
    assert r.gates()["project.test"] == "PASS"  # project.test N/A is not allowed: the check still ran
    assert out.status.value == "SUCCESS"


def test_no_human_wait_paths(tmp_path):
    from magi_system.engine.graph import build_graph

    app = build_graph()
    graph = app.get_graph(xray=True)
    assert "interrupt" not in " ".join(graph.nodes).lower()
    import inspect

    import magi_system.engine as engine_pkg

    src = "".join(inspect.getsource(m) for m in _modules(engine_pkg))
    assert "interrupt(" not in src and "input(" not in src
    r = Run(tmp_path)
    r.start()
    assert r.state["final_status"] is not None
    snap = r.service.graph.get_state({"configurable": {"thread_id": r.run_id}})
    assert snap.next == () and not snap.interrupts


def _modules(pkg):
    import importlib
    import pkgutil

    return [importlib.import_module(f"{pkg.__name__}.{m.name}") for m in pkgutil.iter_modules(pkg.__path__)]
