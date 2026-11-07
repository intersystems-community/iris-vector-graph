"""4.6.0 R188: a WITH alias that is also an SQL word or an IVG column name answers like any other alias.

`MATCH (n:X) WITH n.id AS id RETURN id ORDER BY id` failed with SQLCODE -23 ("Label 'STAGE1' is not listed"), and
`WITH n, n.k AS s RETURN n.k, s ORDER BY s` with -27 (ambiguous). Two causes, both in the final SELECT over the WITH
stage. An alias IRIS reserves (`id`, `key`, `label`, `value`, `count`, `name`, `type`, ...) is selected qualified,
`Stage1."id"`, and IRIS refuses a CTE's own name as a qualifier once the statement has an ORDER BY unless the CTE is
given an alias in FROM. And ORDER BY read the column unqualified, which is ambiguous when the RETURN joins a table
that has a column of that name (`rdf_props` has `s`, `key`, `val`, `graph_id` and the row id `ID`). Without ORDER BY
every alias worked.

The graph, by hand: two nodes labelled `LA<run>`, `{k: 1}` and `{k: 2}`, the first with an edge to the second.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.e2e]

ALIASES = ["id", "s", "p", "o_id", "label", "key", "val", "graph_id", "node_id", "value", "count", "name", "type"]

SHAPES = [
    ("WITH n.k AS {a} RETURN {a} ORDER BY {a}", [[1], [2]]),
    ("WITH n.k AS {a} RETURN {a} ORDER BY {a} DESC", [[2], [1]]),
    ("WITH n, n.k AS {a} RETURN n.k, {a} ORDER BY {a} DESC", [[2, 2], [1, 1]]),
    ("WITH n AS {a} RETURN {a}.k ORDER BY {a}.k", [[1], [2]]),
    ("WITH n.k AS {a} RETURN {a} ORDER BY {a} LIMIT 1", [[1]]),
    ("WITH n.k AS {a} RETURN {a} ORDER BY {a} SKIP 1 LIMIT 5", [[2]]),
    ("WITH n.k AS {a} WHERE {a} > 1 RETURN {a}", [[2]]),
    ("WITH n.k AS {a} RETURN {a}, count(*) AS c ORDER BY {a}", [[1, 1], [2, 1]]),
    ("RETURN n.k AS {a} ORDER BY {a}", [[1], [2]]),
    ("WITH n AS {a} MATCH ({a})-[:TA188]->(m) RETURN m.k", [[2]]),
]


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    label = f"LA{run}"
    a, b = f"r188_{run}_a", f"r188_{run}_b"
    # A Cypher CREATE stores k as a number; create_node stores the text '1', which a WITH column reads back as the
    # string (docs/KNOWN_ISSUES.md, "A property cannot tell a number from a string that spells it")
    r = e.execute_cypher(f"CREATE (:{label} {{id: $a, k: 1}})-[:TA188]->(:{label} {{id: $b, k: 2}})", {"a": a, "b": b})
    assert r.error is None, r.error
    yield e, label, (a, b)
    e.execute_cypher(f"MATCH (n:{label}) DETACH DELETE n")


@pytest.mark.parametrize("alias", ALIASES)
@pytest.mark.parametrize("shape, want", SHAPES, ids=[f"shape{i}" for i in range(len(SHAPES))])
def test_alias(g, alias, shape, want):
    e, label, _ = g
    q = f"MATCH (n:{label}) " + shape.format(a=alias)
    r = e.execute_cypher(q)
    assert r.error is None, (q, r.error)
    assert r.rows == want, q


@pytest.mark.parametrize("alias", ALIASES)
def test_labels_of_a_renamed_node(g, alias):
    """`WITH n AS s RETURN labels(s)` read `FROM rdf_labels WHERE s = s`: every label in the namespace, on each row."""
    e, label, _ = g
    r = e.execute_cypher(f"MATCH (n:{label}) WITH n AS {alias} RETURN labels({alias})")
    assert r.error is None, r.error
    assert [row[0] for row in r.rows] == [[label], [label]]  # 4.6.0 R311: labels() is the list


@pytest.mark.parametrize("alias", ALIASES)
def test_a_renamed_node_is_its_node(g, alias):
    e, label, (a, b) = g
    r = e.execute_cypher(f"MATCH (n:{label}) WITH n AS {alias} RETURN {alias}.id, {alias}.k ORDER BY {alias}.k")
    assert r.error is None, r.error
    assert r.rows == [[a, 1], [b, 2]]


def test_the_reported_query(g):
    e, label, (a, b) = g
    r = e.execute_cypher(f"MATCH (n:{label}) WITH n.id AS id RETURN id ORDER BY id")
    assert r.error is None, r.error
    assert r.rows == [[a], [b]]  # "..._a" sorts before "..._b"
