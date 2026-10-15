"""Versioned prompt registry. Each template declares `version` in its header; the run
records name, version and sha256 of every template so resume can refuse silent changes."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import cache
from importlib import resources

_HEADER = re.compile(r"<!-- prompt: (?P<name>[a-z_]+) \| version: (?P<version>\d+) -->")


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    text: str
    sha256: str

    @property
    def ref(self) -> str:
        return f"{self.name}@v{self.version}#{self.sha256[:12]}"

    def render(self, **values: str) -> str:
        body = _HEADER.sub("", self.text, count=1).strip()
        return body.format(**values) if values else body


@cache
def load(name: str) -> Prompt:
    text = resources.files("magi_system.prompts").joinpath("templates", f"{name}.md").read_text()
    m = _HEADER.match(text)
    if not m or m["name"] != name.lstrip("_"):
        raise ValueError(f"prompt {name!r} has a missing or mismatched version header")
    return Prompt(name=name, version=m["version"], text=text, sha256=hashlib.sha256(text.encode()).hexdigest())


ALL_PROMPTS = (
    "_common",
    "magi_analysis",
    "magi_proposal",
    "magi_cross_review",
    "preventive_review",
    "dev_writer",
    "dev_pair_assessment",
    "dev_pair",
    "specialist_review",
    "tech_writer",
    "magi_final_review",
)


def manifest() -> dict[str, str]:
    return {name: load(name).ref for name in ALL_PROMPTS}
