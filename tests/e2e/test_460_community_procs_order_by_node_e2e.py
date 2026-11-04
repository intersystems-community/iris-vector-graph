"""4.6.0 R105 (Cypher half): `ORDER BY node` after a procedure that yields `node`.

The procedure's CTE names its node column `node` and the RETURN projects it `AS node_id`; `ORDER BY node` translated
the variable as a table node's (`<alias>.node_id`), then dropped the alias, and so sorted on
`CY_SORT_KEY(node_id, 'x')`: the SELECT list's alias inside an expression, which IRIS does not resolve
(SQLCODE -29, field not found). It now reads the CTE's own column. `node` still comes back as a node (ID, labels,
properties, then the yielded columns), so the first column of a row is the ID.

Worked by hand: a -> b, b -> c, c -> a and a -> d. Direction ignored, a has neighbours b, c, d; b and c each have
the other and a; d has a alone. Removing d (degree 1) leaves the triangle, each of degree 2: k-core gives a, b, c
coreness 2 and d coreness 1.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.e2e]

KCORE = "CALL ivg.kcore({topK: 100}) YIELD node, coreness RETURN node, coreness"


@pytest.fixture
def engine(iris_connection, iris_master_cleanup):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    for nid in "abcd":
        e.create_node(nid, labels=["V"])
    for s, o in (("a", "b"), ("b", "c"), ("c", "a"), ("a", "d")):
        e.create_edge(s, "R", o)
    e.sync()
    return e


def _pairs(engine, query):
    rows = engine.execute_cypher(query, raise_on_error=True).rows
    return [(row[0], row[-1]) for row in rows]


def test_order_by_node_ascending(engine):
    assert _pairs(engine, KCORE + " ORDER BY node") == [("a", 2), ("b", 2), ("c", 2), ("d", 1)]


def test_order_by_node_descending(engine):
    assert _pairs(engine, KCORE + " ORDER BY node DESC") == [("d", 1), ("c", 2), ("b", 2), ("a", 2)]


def test_order_by_a_yielded_column_then_node(engine):
    assert _pairs(engine, KCORE + " ORDER BY coreness, node") == [("d", 1), ("a", 2), ("b", 2), ("c", 2)]


def test_order_by_node_after_degree_centrality(engine):
    """Another procedure on the same path. Only the order is asserted: the procedure's `degree` reads 0 for every
    node here (the `''` predicate it passes is not the method's empty string), a separate defect."""
    rows = engine.execute_cypher(
        "CALL ivg.degreeCentrality({direction: 'out'}) YIELD node, degree RETURN node, degree ORDER BY node DESC",
        raise_on_error=True,
    ).rows
    assert [row[0] for row in rows] == ["d", "c", "b", "a"]
