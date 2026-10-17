"""Build the loop graph. Build only; the one product compile lives in runner.py."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.business_query.loop.context import LoopContext
from app.business_query.loop.nodes.admit_selection import (
    admit_selection_node,
    prepare_bq_node,
)
from app.business_query.loop.nodes.document_search import document_search_node
from app.business_query.loop.nodes.reduce import reduce_node
from app.business_query.loop.nodes.sql_set import sql_set_node
from app.business_query.loop.state import LoopState

NODES_PER_TURN = 5


def recursion_limit_for(iteration_cap: int) -> int:
    """A backstop, never the stop mechanism: reaching it is a defect."""
    del iteration_cap
    return NODES_PER_TURN + 2


def build_loop_graph() -> StateGraph:
    graph = StateGraph(LoopState, context_schema=LoopContext)
    graph.add_node("prepare", prepare_bq_node)
    graph.add_node("admit", admit_selection_node)
    graph.add_node("sql_set", sql_set_node)
    graph.add_node("document_search", document_search_node)
    graph.add_node("reduce", reduce_node)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "admit")
    graph.add_edge("admit", "sql_set")
    graph.add_edge("admit", "document_search")
    graph.add_edge("sql_set", "reduce")
    graph.add_edge("document_search", "reduce")
    graph.add_edge("reduce", END)
    return graph
