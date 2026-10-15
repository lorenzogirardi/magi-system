"""Main StateGraph and subgraphs A–G (brief §5).

    START → intake(A) → magi(B) → implementation(C) → triage ⇄ verification(D)
                                                          ⇄ documentation(E)
                                                          ⇄ final_review(F)
                                                          ⇄ implementation(C) / magi(B)
                                                     → gate(G) → END

Routing is code (`route_*` functions). There is no LLM supervisor, no interrupt and no
node that waits for a human: every path ends in `gate`, which sets a terminal status.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from magi_system.engine import nodes_gate as G
from magi_system.engine import nodes_impl as C
from magi_system.engine import nodes_intake as A
from magi_system.engine import nodes_magi as B
from magi_system.engine import nodes_verify as D
from magi_system.engine.context import RunContext
from magi_system.engine.state import MagiState


def _sg() -> StateGraph:
    return StateGraph(MagiState, context_schema=RunContext)


def build_magi_subgraph():
    g = _sg()
    g.add_node("magi_begin", B.magi_begin)
    g.add_node("magi_analyze", B.magi_analyze)
    g.add_node("magi_analysis_collect", B.magi_analysis_collect)
    g.add_node("magi_propose", B.magi_propose)
    g.add_node("magi_proposal_collect", B.magi_proposal_collect)
    g.add_node("magi_review", B.magi_review)
    g.add_node("magi_review_collect", B.magi_review_collect)
    g.add_node("magi_verify", B.magi_verify)
    g.add_node("magi_select", B.magi_select)
    g.add_edge(START, "magi_begin")
    g.add_conditional_edges("magi_begin", B.route_begin, ["magi_analyze", "magi_propose"])
    g.add_edge("magi_analyze", "magi_analysis_collect")
    g.add_conditional_edges("magi_analysis_collect", B.route_analysis, ["magi_analyze", "magi_propose", END])
    g.add_edge("magi_propose", "magi_proposal_collect")
    g.add_conditional_edges("magi_proposal_collect", B.route_proposal, ["magi_propose", "magi_review", END])
    g.add_edge("magi_review", "magi_review_collect")
    g.add_conditional_edges("magi_review_collect", B.route_review, ["magi_review", "magi_verify", END])
    g.add_edge("magi_verify", "magi_select")
    g.add_conditional_edges("magi_select", B.route_select, {"magi_begin": "magi_begin", "__end__": END})
    return g.compile(name="magi")


def build_implementation_subgraph():
    g = _sg()
    g.add_node("dev_writer", C.dev_writer)
    g.add_node("dev_initial_checks", C.dev_initial_checks)
    g.add_node("dev_pair_assess", C.dev_pair_assess)
    g.add_node("dev_pair", C.dev_pair)
    g.add_node("impl_technical", C.impl_technical)
    g.add_conditional_edges(START, C.route_entry, ["dev_writer", "dev_pair_assess"])
    g.add_conditional_edges("dev_writer", C.route_after_writer, {"impl_technical": "impl_technical", "dev_initial_checks": "dev_initial_checks", "__end__": END})
    g.add_edge("dev_initial_checks", "dev_pair_assess")
    g.add_conditional_edges("dev_pair_assess", C.route_after_assess, ["impl_technical", "dev_pair"])
    g.add_conditional_edges("dev_pair", C.route_after_pair, {"impl_technical": "impl_technical", "__end__": END})
    g.add_conditional_edges("impl_technical", C.route_technical, {"dev_writer": "dev_writer", "dev_pair_assess": "dev_pair_assess", "__end__": END})
    return g.compile(name="implementation")


def build_verification_subgraph():
    g = _sg()
    g.add_node("verify_checks", D.verify_checks)
    g.add_node("specialist_review", D.specialist_review)
    g.add_node("specialist_collect", D.specialist_collect)
    g.add_edge(START, "verify_checks")
    g.add_conditional_edges("verify_checks", D.route_to_specialists, ["specialist_review"])
    g.add_edge("specialist_review", "specialist_collect")
    g.add_conditional_edges("specialist_collect", D.route_specialists, ["specialist_review", END])
    return g.compile(name="verification")


def build_documentation_subgraph():
    g = _sg()
    g.add_node("tech_writer", D.tech_writer)
    g.add_edge(START, "tech_writer")
    g.add_edge("tech_writer", END)
    return g.compile(name="documentation")


def build_final_review_subgraph():
    g = _sg()
    g.add_node("final_start", lambda state: {})
    g.add_node("final_review", D.final_review)
    g.add_node("final_collect", D.final_collect)
    g.add_edge(START, "final_start")
    g.add_conditional_edges("final_start", D.route_to_final_review, ["final_review"])
    g.add_edge("final_review", "final_collect")
    g.add_conditional_edges("final_collect", D.route_final, ["final_review", END])
    return g.compile(name="final_review")


def _after_intake(state: dict) -> str:
    return "gate" if state.get("final_status") else "magi"


def _after_magi(state: dict) -> str:
    return "gate" if state.get("final_status") or not state.get("selected_plan") else "implementation"


def _after_stage(state: dict) -> str:
    return "gate" if state.get("final_status") else "triage"


def build_graph(checkpointer=None):
    g = _sg()
    g.add_node("intake", A.intake)
    g.add_node("magi", build_magi_subgraph())
    g.add_node("implementation", build_implementation_subgraph())
    g.add_node("verification", build_verification_subgraph())
    g.add_node("documentation", build_documentation_subgraph())
    g.add_node("final_review", build_final_review_subgraph())
    g.add_node("triage", G.triage)
    g.add_node("gate", G.gate)
    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", _after_intake, ["magi", "gate"])
    g.add_conditional_edges("magi", _after_magi, ["implementation", "gate"])
    for stage in ("implementation", "verification", "documentation", "final_review"):
        g.add_conditional_edges(stage, _after_stage, ["triage", "gate"])
    g.add_conditional_edges("triage", G.route_triage, ["magi", "implementation", "verification", "documentation", "final_review", "gate"])
    g.add_edge("gate", END)
    return g.compile(checkpointer=checkpointer, name="magi-system")
