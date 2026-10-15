"""Offline mode: a built-in demo target and a scripted "happy path" for the fake model.

Used by `magi demo`, `magi run --offline` and the tests. The scripted answers demonstrate
orchestration behavior only; they say nothing about the quality of real models.

The scenario targets the greeter fixture (a POSIX shell + Makefile project, deliberately a
different stack from magi-system). Responders are stateless functions of the request, so
they behave the same after a resume.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from magi_system.llm.base import ModelRequest, ScriptedModel
from magi_system.models.common import MAGI_IDS, AgentId, stable_id
from magi_system.workspace.git import git

DEMO_FILES = {
    "Makefile": "build:\n\tsh -n src/greet.sh\n\ntest:\n\tsh tests/run.sh\n\nlint:\n\tsh -n src/greet.sh && sh -n tests/run.sh\n",
    "src/greet.sh": '#!/bin/sh\n# Prints a greeting.\ngreet() {\n  echo "Hello, $1"\n}\n',
    "tests/run.sh": (
        "#!/bin/sh\nset -e\n. ./src/greet.sh\n"
        '[ "$(greet World)" = "Hello, World" ] || { echo "FAIL greet"; exit 1; }\n'
        'echo "ok greet"\n'
    ),
    "README.md": "# greeter\n\nA tiny shell greeter.\n",
    "docs/usage.md": "# Usage\n\nSource `src/greet.sh` and call `greet NAME`.\n",
}

DEMO_QUOTE = "Add a farewell function to src/greet.sh that prints 'Goodbye, NAME'"
DEMO_REQUEST = f"{DEMO_QUOTE}, cover it with a test, and document it.\n"

FAREWELL = 'farewell() {\n  echo "Goodbye, $1"\n}\n'
FAREWELL_TEST = '[ "$(farewell World)" = "Goodbye, World" ] || { echo "FAIL farewell"; exit 1; }\necho "ok farewell"\n'
DOC_TEXT = "\n## farewell\n\nCall `farewell NAME` to print `Goodbye, NAME`.\n"


def create_demo_repo(root: Path, files: dict[str, str] | None = None) -> Path:
    """Create a fresh git repository with the demo target (outside magi-system)."""
    root.mkdir(parents=True, exist_ok=False)
    git("init", "--quiet", "-b", "main", cwd=root)
    for rel, text in (files or DEMO_FILES).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git("add", "-A", cwd=root)
    git("commit", "--quiet", "-m", "initial", cwd=root)
    return root


def requirement_id(quote: str) -> str:
    """Same id the engine derives for an explicit requirement quoted from the request."""
    return stable_id("req", "explicit", " ".join(quote.split()).lower())


def block(prompt: str, header: str):
    """Parse the JSON inside the untrusted-data block following `## {header}`."""
    m = re.search(rf"## {re.escape(header)}[^\n]*\n<untrusted-data>\n(.*?)\n</untrusted-data>", prompt, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return m.group(1)


def tool_turn(actions: list[dict], final: dict) -> Callable[[ModelRequest], dict]:
    """First turn returns `actions`, the next one (after tool results) returns `final`."""

    def respond(req: ModelRequest) -> dict:
        if "Tool results:" in req.prompt or not actions:
            return {"final": final}
        return {"actions": actions}

    return respond


def _analysis(req: ModelRequest) -> dict:
    return {"final": {
        "understanding": "Add a farewell function next to greet, with a test and docs.",
        "requirements": [{"text": "farewell NAME prints 'Goodbye, NAME'", "kind": "explicit", "source_quote": DEMO_QUOTE}],
        "claims": [
            {"kind": "file_exists", "statement": "src/greet.sh exists", "target": "src/greet.sh", "critical": True},
            {"kind": "symbol_exists", "statement": "greet is defined", "target": "src/greet.sh::greet"},
        ],
        "open_questions": [], "risks": [],
    }}


PROPOSAL_STYLE = {
    AgentId.MELCHIOR.value: ("Extend greet.sh in place", []),
    AgentId.BALTHASAR.value: ("Extend greet.sh with a guarded function", []),
    AgentId.CASPER.value: ("New farewell.sh module", [{"path": "src/farewell.sh", "action": "create", "description": "new module"}]),
}


def _proposal(req: ModelRequest) -> dict:
    title, extra = PROPOSAL_STYLE[req.agent]
    return {"final": {
        "title": title, "summary": f"{title}; add a test to tests/run.sh.",
        "coverage": [{"requirement_id": requirement_id(DEMO_QUOTE), "covered": True, "how": "farewell function + test"}],
        "claims": [{"kind": "symbol_exists", "statement": "greet exists to sit next to", "target": "src/greet.sh::greet", "critical": True}],
        "changes": [{"path": "src/greet.sh", "action": "modify", "description": "add farewell()"},
                    {"path": "tests/run.sh", "action": "modify", "description": "assert farewell output"}, *extra],
        "verification_plan": ["make test", "make lint"], "migration_rollback": "revert the commit; additive change",
        "documentation_impact": ["docs/usage.md"], "tradeoffs": [],
    }}


def _scores(base: int) -> list[dict]:
    return [{"criterion": c, "score": base, "rationale": "ok"} for c in
            ("requirement_fit", "simplicity", "risk", "operability", "testability", "maintainability")]


def _cross_review(req: ModelRequest) -> dict:
    shown = block(req.prompt, "Proposals (author masked)") or {}
    out = []
    for label, p in sorted(shown.items()):
        out.append({"label": label, "scores": _scores(3 if len(p["changes"]) > 2 else 4), "supports": True, "findings": []})
    return {"assessments": out}


def _preventive(req: ModelRequest) -> dict:
    shown = block(req.prompt, "Proposals (author masked)") or {}
    out: dict = {"reviews": [{"label": label, "findings": []} for label in sorted(shown)]}
    if req.agent == AgentId.QA.value:
        out["acceptance_scenarios"] = [{"id": "farewell-prints-goodbye", "description": "farewell World prints Goodbye, World",
                                        "requirement_ids": [requirement_id(DEMO_QUOTE)], "command": "make test"}]
    if req.agent == AgentId.TECH_WRITER.value:
        out["documentation_impact"] = ["docs/usage.md: reference entry for farewell"]
    if req.agent == AgentId.SRE.value:
        out["not_applicable_reason"] = "library shell function; nothing deployed or operated"
    return out


def _candidate_review(req: ModelRequest) -> dict:
    if req.agent == AgentId.SRE.value:
        return {"findings": [], "not_applicable_reason": "library shell function; nothing deployed or operated"}
    return {"findings": [], "summary": "no issues"}


def _writer_actions(ws_read: Callable[[str], str] | None = None) -> list[dict]:
    greet = DEMO_FILES["src/greet.sh"] + FAREWELL
    tests = DEMO_FILES["tests/run.sh"] + FAREWELL_TEST
    return [{"tool": "write_file", "args": {"path": "src/greet.sh", "content": greet}},
            {"tool": "write_file", "args": {"path": "tests/run.sh", "content": tests}},
            {"tool": "run_command", "args": {"command": "make test"}}]


def happy_path() -> dict[tuple[str, str], Callable]:
    """Scripts keyed by (agent id, purpose). Tests replace individual entries."""
    scripts: dict[tuple[str, str], Callable] = {}
    for m in MAGI_IDS:
        scripts[(m.value, "analysis")] = _analysis
        scripts[(m.value, "proposal")] = _proposal
        scripts[(m.value, "cross_review")] = _cross_review
        scripts[(m.value, "final_review")] = lambda req: {"findings": [], "summary": "adheres to the request"}
    for s in (AgentId.QA, AgentId.SRE, AgentId.SECURITY, AgentId.TECH_WRITER):
        scripts[(s.value, "preventive_review")] = _preventive
    for s in (AgentId.QA, AgentId.SRE, AgentId.SECURITY):
        scripts[(s.value, "candidate_review")] = _candidate_review
    scripts[(AgentId.DEV1.value, "dev_writer")] = tool_turn(_writer_actions(), {"summary": "added farewell and its test", "checks_run": ["make test"]})
    scripts[(AgentId.DEV2.value, "pair_assessment")] = lambda req: {"assessment": "diff adds farewell and a test; looks minimal", "concerns": []}
    scripts[(AgentId.DEV2.value, "dev_pair")] = tool_turn([{"tool": "run_command", "args": {"command": "make test"}}],
                                                          {"summary": "tests pass; no changes needed", "checks_run": ["make test"]})
    scripts[(AgentId.TECH_WRITER.value, "documentation")] = tool_turn(
        [{"tool": "write_file", "args": {"path": "docs/usage.md", "content": DEMO_FILES["docs/usage.md"] + DOC_TEXT}}],
        {"summary": "documented farewell", "updates": [{"path": "docs/usage.md", "kind": "reference", "summary": "farewell"}]},
    )
    return scripts


def scripted_models(scripts: dict[tuple[str, str], Callable] | None = None) -> dict[AgentId, ScriptedModel]:
    model = ScriptedModel(scripts=dict(scripts or happy_path()))
    return {a: model for a in AgentId}
