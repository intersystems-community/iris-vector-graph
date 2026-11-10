"""Shared by tests/e2e/test_470_{ppr_args,ppr_every_node,vecindex_cosine,leiden_topk}_e2e.py: a graph written into the
scratch namespace `IVG_NAMESPACE` (tests/e2e/ns463.py), erasing the one there first."""

from __future__ import annotations

import pytest

from tests.e2e import ns463


def native(c):
    import iris

    return iris.createIRIS(c)


@pytest.fixture(scope="module")
def conn():
    c = ns463.connect()
    yield c
    native(c).classMethodValue("Graph.KG.Eraser", "EraseAll")
    c.close()


def load(c, nodes, edges):
    """Erase the namespace's graph and write this one; the engines as a caller builds them (ns463.engines)."""
    native(c).classMethodValue("Graph.KG.Eraser", "EraseAll")
    engs = ns463.engines(c)
    eng = engs["default"]
    for n in nodes:
        eng.create_node(n)
    for s, p, o in edges:
        assert eng.create_edge(s, p, o)
    eng.sync()
    assert eng.node_count() == len(nodes), "the graph did not load"
    return engs


def call_scores(eng, query):
    """``{first column: float(second)}`` and the row count."""
    r = eng.execute_cypher(query, {}, raise_on_error=True)
    return {row[0]: float(row[1]) for row in r.rows}, len(r.rows)
