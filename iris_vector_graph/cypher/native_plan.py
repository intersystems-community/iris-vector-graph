"""The planner for natively executed read-only Cypher (spec 253).

`plan_query` turns a parsed query into a pipeline plan the ObjectScript executor (Graph.KG.Cypher.*) runs, or answers
None, and the query takes today's SQL path unchanged (FR-001). It declines anything outside the supported set; the
set grows phase by phase (tasks.md), each family only after its shadow gate.

`native_gate` is the engine-side check that a plan may run at all (FR-003, FR-005, FR-011, FR-014).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from . import ast


def plan_query(query: ast.CypherQuery, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The native plan for `query`, or None for the SQL path. Phase 1: no shape is supported yet."""
    if query.is_mutation or query.procedure_call is not None or query.union_queries:
        return None
    return None


def native_gate(engine) -> Optional[str]:
    """None when a native plan may run on this engine, else the reason it may not."""
    if not getattr(engine, "native_cypher", False):
        return "native_cypher=False"
    if getattr(engine, "_adjacency_indexed", False) is not True:
        return "adjacency index not declared"
    if getattr(engine, "_in_bulk_load", False):
        return "inside bulk_load_session"
    if not engine._native_privileges():
        return "missing SELECT on a Graph_KG table"
    return None
