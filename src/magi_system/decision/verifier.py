"""Tool-based claim verification (brief §8). The verifier uses the repository, the
runner and the policy — never another LLM's opinion.

Outcomes:
- SUPPORTED / REFUTED for mechanically checkable claims;
- UNVERIFIABLE for operational guarantees and business assumptions (they can never be
  self-authorized by agents and therefore count as unresolved critical unknowns when
  critical).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from magi_system.execution.runner import ExecutionRunner
from magi_system.models.common import AgentId, ArtifactMeta, Phase, Verdict, stable_id
from magi_system.models.decision import VerificationResult
from magi_system.models.knowledge import Claim, ClaimKind, ClaimStatus
from magi_system.policy.engine import PolicyEngine, PolicyError, normalize_rel_path

_SYMBOL_DEF = (
    r"(?:^|\s)(?:def|class|function|func|fn|const|let|var|type|interface|struct|enum)\s+{name}\b",
    r"^\s*{name}\s*\(\s*\)\s*\{{",  # shell function
    r"^\s*{name}\s*[:=]",
    r"^{name}:",  # Makefile target
)
_PKG = re.compile(
    r"^(?P<name>[A-Za-z0-9_.@/-]+)(?:\[[^\]]*\])?(?:\s*(?P<op>>=|==)\s*(?P<ver>[0-9][0-9A-Za-z.]*))?$"
)
_MANIFESTS = ("package.json", "pyproject.toml", "requirements.txt")


def _version_tuple(v: str) -> tuple:
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"[.+-]", v))


@dataclass
class ClaimVerifier:
    workspace: Path
    policy: PolicyEngine
    runner: ExecutionRunner | None
    commit: str
    round: int
    phase: Phase = Phase.CLAIM_VERIFICATION

    def _read(self, rel: str) -> str | None:
        try:
            rel = normalize_rel_path(rel)
        except PolicyError:
            return None
        p = (self.workspace / rel).resolve()
        try:
            p.relative_to(self.workspace.resolve())
        except ValueError:
            return None
        if ".git" in Path(rel).parts or not p.is_file():
            return None
        return p.read_text(errors="replace")

    def _symbol_found(self, target: str) -> tuple[bool, str]:
        path, _, symbol = target.partition("::")
        if not symbol:
            return False, "symbol claims must use 'path::symbol' (or '*::symbol')"
        name = re.escape(symbol.split(".")[-1])
        patterns = [re.compile(p.format(name=name), re.MULTILINE) for p in _SYMBOL_DEF]
        files = [path] if path != "*" else [
            str(p.relative_to(self.workspace)) for p in self.workspace.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(self.workspace).parts
        ]
        for rel in files:
            text = self._read(rel)
            if text and any(p.search(text) for p in patterns):
                return True, f"definition of {symbol!r} found in {rel}"
        return False, f"no definition of {symbol!r} in {path}"

    def _package(self, target: str) -> tuple[bool | None, str]:
        m = _PKG.match(target.strip().rsplit("::", 1)[-1])  # also "requirements.txt::fastapi"
        if not m:
            return None, f"unparseable package target {target!r}"
        name, op, ver = m["name"], m["op"], m["ver"]
        import json
        import tomllib

        manifests = {
            "package.json": lambda t: {**json.loads(t).get("dependencies", {}), **json.loads(t).get("devDependencies", {})},
            "pyproject.toml": lambda t: {
                re.split(r"[<>=!~ \[]", d, maxsplit=1)[0]: d for d in tomllib.loads(t).get("project", {}).get("dependencies", [])
            },
            "requirements.txt": lambda t: {
                re.split(r"[<>=!~ \[;]", d, maxsplit=1)[0]: d
                for d in (line.split("#", 1)[0].strip() for line in t.splitlines()) if d and not d.startswith("-")
            },
        }
        for manifest, parse in manifests.items():
            text = self._read(manifest)
            if text is None:
                continue
            try:
                deps = parse(text)
            except Exception:
                continue
            if name in deps:
                if not ver:
                    return True, f"{name} declared in {manifest}"
                declared = re.findall(r"[0-9][0-9A-Za-z.]*", str(deps[name]))
                if op == "==":
                    if declared and declared[0] == ver:
                        return True, f"{name} pinned to {ver} in {manifest}"
                    return False, f"{name} declared as {deps[name]!r} in {manifest}, not =={ver}"
                if declared and _version_tuple(declared[0]) >= _version_tuple(ver):
                    return True, f"{name} {declared[0]} >= {ver} in {manifest}"
                return False, f"{name} declared as {deps[name]!r} in {manifest}; capability >= {ver} not shown"
        return False, f"{name} not declared in any manifest"

    def _policy(self, key: str) -> tuple[bool, str]:
        node: object = self.policy.effective
        for part in key.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and part in [str(x) for x in node]:
                node = part
            else:
                return False, f"policy key {key!r} does not exist in policy {self.policy.version}"
        return True, f"policy key {key!r} exists"

    def verify(self, claim: Claim) -> tuple[Claim, VerificationResult]:
        found: bool | None
        if claim.kind is ClaimKind.FILE_EXISTS:
            found = self._read(claim.target) is not None
            detail = f"file {claim.target} {'exists' if found else 'does not exist'} at {self.commit[:12]}"
        elif claim.kind is ClaimKind.SYMBOL_EXISTS:
            found, detail = self._symbol_found(claim.target)
        elif claim.kind is ClaimKind.TEXT_PRESENT:
            text = self._read(claim.target)
            # Models usually quote code literally ("f(x)", "[a]"): a literal match counts first,
            # so regex metacharacters cannot turn a true quote into a refutation. A pattern that
            # is not a valid regex can only be meant literally.
            found = text is not None and claim.pattern in text
            if text is not None and not found:
                try:
                    found = re.search(claim.pattern, text, re.MULTILINE) is not None
                except re.error:
                    found = False
            detail = f"pattern {claim.pattern!r} {'found' if found else 'not found'} in {claim.target}"
        elif claim.kind is ClaimKind.PACKAGE_VERSION:
            # Also accepted: target = a manifest file, pattern = the spec ("fastapi==0.143.1").
            spec = claim.pattern if claim.pattern and Path(claim.target).name in _MANIFESTS else claim.target
            found, detail = self._package(spec)
        elif claim.kind is ClaimKind.POLICY_EXISTS:
            found, detail = self._policy(claim.target)
        elif claim.kind is ClaimKind.COMMAND_SUCCEEDS:
            if self.runner is None:
                found, detail = None, "no runner available"
            else:
                r = self.runner.run(claim.target, cwd=self.workspace)
                if r.denied:
                    found, detail = None, f"command denied by policy: {r.denial_reason}"
                elif r.timed_out:
                    found, detail = None, "command timed out"
                else:
                    found, detail = r.exit_code == 0, f"exit code {r.exit_code}"
        else:
            found, detail = None, f"{claim.kind.value} claims cannot be verified mechanically"

        if found is None:
            status, verdict = ClaimStatus.UNVERIFIABLE, Verdict.UNKNOWN
        else:
            holds = found == claim.expect
            status = ClaimStatus.SUPPORTED if holds else ClaimStatus.REFUTED
            verdict = Verdict.PASS if holds else Verdict.FAIL
        result = VerificationResult(
            meta=ArtifactMeta(
                id=stable_id("vr", claim.id, self.commit),
                producer=AgentId.VERIFIER, round=self.round, phase=self.phase,
                input_hash=self.commit, evidence_refs=(),
            ),
            subject_id=claim.id, check=f"claim:{claim.kind.value}", verdict=verdict, detail=detail,
            candidate_commit=self.commit,
        )
        return claim.model_copy(update={"status": status, "evidence_refs": (result.meta.id,)}), result
