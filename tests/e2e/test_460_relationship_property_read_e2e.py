"""4.6.0 R062: a relationship property named `w`, `weight` or `ts` reads its stored value, not NULL.

The translator answered `r.w`, `r.weight` and `r.ts` with the literal `NULL` (and a warning) unless the query also
filtered `WHERE r.ts` (the temporal route): `MATCH (a:X)-[r:R]->(b) RETURN r.w AS w` was `SELECT NULL AS w`, in WHERE
too (`WHERE r.w = 5` became `NULL = 5`, no rows). `properties(r)` answered `{"w": 5}` all along. Any other name
(`r.label`) read the qualifiers. The TCK never names a relationship property w, weight or ts, so it could not see it.

Values are the ones each test writes.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    label, rel = f"Rp460{run}", f"RP460_{run}"
    r = engine.execute_cypher(
        f"CREATE (a:{label} {{name: 'a'}})-[r:{rel} {{w: 5, weight: 2.5, ts: 100, tag: 'hot'}}]->(b:{label} {{name: 'b'}}) "
        "RETURN r.w, r.weight, r.ts, r.tag"
    )
    assert not getattr(r, "error", None), r.error
    created = r.rows
    ids = {k: f"rp460_{run}_{k}" for k in ("s", "o")}
    for nid in ids.values():
        engine.create_node(nid, labels=[label])
    engine.create_edge(ids["s"], rel, ids["o"], qualifiers={"w": 7, "note": "s"})
    with contextlib.suppress(Exception):
        iris_connection.commit()

    class G:
        pass

    graph = G()
    graph.engine, graph.label, graph.rel, graph.ids, graph.created = engine, label, rel, ids, created
    yield graph
    engine.execute_cypher(f"MATCH (n:{label}) DETACH DELETE n")


def _ask(g, q, params=None):
    r = g.engine.execute_cypher(q, params or {})
    assert not getattr(r, "error", None), (q, r.error)
    return r


def test_the_create_returns_what_it_wrote(g):
    assert g.created == [[5, 2.5, 100, "hot"]]


def test_typed_aliased(g):
    r = _ask(g, f"MATCH (a:{g.label} {{name: 'a'}})-[r:{g.rel}]->(b) RETURN r.w AS w, r.weight AS wt, r.ts AS ts")
    assert (r.columns, r.rows) == (["w", "wt", "ts"], [[5, 2.5, 100]])


def test_typed_unaliased_string_and_integer(g):
    r = _ask(g, f"MATCH (a:{g.label} {{name: 'a'}})-[r:{g.rel}]->(b) RETURN r.w, r.tag")
    assert (r.columns, r.rows) == (["r.w", "r.tag"], [[5, "hot"]])


def test_untyped(g):
    r = _ask(g, "MATCH (a {node_id: $s})-[r]->(b) RETURN r.w AS w, r.note AS note", {"s": g.ids["s"]})
    assert r.rows == [[7, "s"]]


def test_in_where(g):
    r = _ask(g, f"MATCH (a:{g.label})-[r:{g.rel}]->(b) WHERE r.w = 5 RETURN b.name AS b")
    assert r.rows == [["b"]]
    # (`WHERE r.ts ...` is the temporal route's trigger, which reads the temporal index, not this row)
    r = _ask(g, f"MATCH (a:{g.label})-[r:{g.rel}]->(b) WHERE r.weight > 2 AND r.w < 6 RETURN a.name AS a")
    assert r.rows == [["a"]]
    r = _ask(g, f"MATCH (a:{g.label})-[r:{g.rel}]->(b) WHERE r.weight > 3 RETURN a.name AS a")
    assert r.rows == []


def test_properties_agrees(g):
    r = _ask(g, f"MATCH (a:{g.label} {{name: 'a'}})-[r:{g.rel}]->(b) RETURN properties(r) AS p")
    import json

    value = r.rows[0][0]
    assert (json.loads(value) if isinstance(value, str) else value) == {"w": 5, "weight": 2.5, "ts": 100, "tag": "hot"}
