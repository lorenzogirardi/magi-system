"""Agent roster: stable internal ids, public display names, perspectives."""

from __future__ import annotations

from dataclasses import dataclass

from magi_system.models.common import DISPLAY_NAMES, AgentId


@dataclass(frozen=True)
class AgentProfile:
    id: AgentId
    perspective: str
    final_review_focus: str = ""

    @property
    def display_name(self) -> str:
        return DISPLAY_NAMES[self.id]


PROFILES: dict[AgentId, AgentProfile] = {
    AgentId.MELCHIOR: AgentProfile(
        AgentId.MELCHIOR,
        "evolution of the existing system; reuse and simplicity; minimum necessary change; "
        "compatibility and implementability",
        "adherence to the request, compatibility and scope",
    ),
    AgentId.BALTHASAR: AgentProfile(
        AgentId.BALTHASAR,
        "systemic behavior; data consistency; failure modes and dependencies; observability, "
        "recovery and operability",
        "systemic coherence and failure modes",
    ),
    AgentId.CASPER: AgentProfile(
        AgentId.CASPER,
        "challenging assumptions; architectural alternatives; trust boundaries and enterprise "
        "impact; hidden complexity and unnecessary scope",
        "residual assumptions and overlooked risks",
    ),
    AgentId.QA: AgentProfile(AgentId.QA, "acceptance criteria derived from the original request; test adequacy"),
    AgentId.SRE: AgentProfile(AgentId.SRE, "deployment, failure recovery, configuration and observability"),
    AgentId.SECURITY: AgentProfile(AgentId.SECURITY, "trust boundaries, authorization, secrets and data flows"),
    AgentId.TECH_WRITER: AgentProfile(AgentId.TECH_WRITER, "documentation impact and structure (Diátaxis)"),
    AgentId.DEV1: AgentProfile(AgentId.DEV1, "implementation of the selected plan"),
    AgentId.DEV2: AgentProfile(AgentId.DEV2, "pair review and local fixes"),
}
