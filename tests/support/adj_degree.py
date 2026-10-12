"""Out-degree for live tests, read the one way the product reads it (spec 249).

`^KG("deg")` and `^KG("degp")` no longer exist. A live test that used to assert a stored counter asserts
`Graph.KG.Adj.Degree` instead, which counts the `out` entries behind a generation-validated cache, or asserts that the
derived subtrees are gone. These helpers take either an engine or a Native API object so a test does not care which
handle it already holds.

The graph key is the derived index key: the integer ``0`` for the default graph, the name itself otherwise
(`iris_vector_graph._validate.graph_index_key`, ADR-0003). Tests that already hold the key pass it through.

Needs a live IRIS (`Graph.KG.Adj` compiled); this module itself imports nothing from IRIS.
"""

from __future__ import annotations

from typing import Any


def _native(handle: Any) -> Any:
    return handle._iris_obj() if hasattr(handle, "_iris_obj") else handle


def degree(handle: Any, graph_key: Any, src: str, pred: str = "") -> int:
    """`Graph.KG.Adj.Degree(graph_key, src, pred)` as an int; 0 for a node with no outgoing edge."""
    raw = _native(handle).classMethodValue("Graph.KG.Adj", "Degree", graph_key, src, pred)
    return int(raw) if raw not in (None, "") else 0


def subtree_defined(handle: Any, *subs: Any) -> bool:
    """True when anything is stored at or under ``^KG(subs...)``."""
    return int(_native(handle).isDefined("^KG", *subs)) != 0


def derived_degree_stores_empty(handle: Any, graph_key: Any) -> bool:
    """True when the generation and the degree cache hold nothing for the graph (an erased graph leaves none)."""
    return not subtree_defined(handle, "gen", graph_key) and not subtree_defined(handle, "degc", graph_key)
