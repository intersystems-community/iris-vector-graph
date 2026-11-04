"""4.6.0 R093: a relationship property between -1 and 1 reads back as a number, and a string stays a string.

`create_edge(qualifiers={"confidence": 0.95})` stores the JSON number 0.95 in `rdf_edges.qualifiers`. `RETURN
r.confidence` read it through IVG's `JSON_VALUE` function, whose ObjectScript `%Get` answers the canonical ObjectScript
number `.95`; `prop_values.parse_rel_prop_text` promotes only JSON-shaped number text (`0.95`), so the row was the
string `'.95'`. The JSON string ".95" reads back as the same `.95`, so the text alone cannot say which was stored:
the RETURN projection reads the leaf's JSON type.

The values below are the ones the fixture writes; each answer is that value, with its Python type.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

WRITTEN = {
    "conf": 0.95,
    "neg": -0.5,
    "half": 0.5,
    "big": 2.5,
    "zero": 0,
    "negzero": -0.0,
    "tiny": 1e-7,
    "txt": ".95",
    "flag": True,
}


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    rel = f"RF460_{run}"
    ids = {k: f"rf460_{run}_{k}" for k in ("a", "b", "c")}
    for nid in ids.values():
        engine.create_node(nid, labels=["Rf460"])
    engine.create_edge(ids["a"], rel, ids["b"], qualifiers=WRITTEN)
    engine.create_edge(ids["a"], rel, ids["c"], qualifiers={"conf": 0.25, "txt": "x"})
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield engine, ids, rel
    for nid in ids.values():
        with contextlib.suppress(Exception):
            engine.delete_node(nid)


def _rows(g, q, params=None):
    engine, ids, rel = g
    r = engine.execute_cypher(q.replace("REL", rel), dict(params or {}, a=ids["a"], b=ids["b"]))
    assert not getattr(r, "error", None), (q, r.error)
    return r.rows


@pytest.mark.parametrize(
    "key, expected",
    [
        ("conf", 0.95),
        ("neg", -0.5),
        ("half", 0.5),
        ("big", 2.5),
        ("zero", 0),
        ("negzero", 0),
        ("tiny", 1e-7),
    ],
)
def test_a_number_reads_back_as_that_number(g, key, expected):
    rows = _rows(g, f"MATCH ({{node_id: $a}})-[r:REL]->({{node_id: $b}}) RETURN r.{key}")
    assert rows == [[expected]]
    assert isinstance(rows[0][0], (int, float)) and not isinstance(rows[0][0], bool), rows


def test_aliased(g):
    assert _rows(g, "MATCH ({node_id: $a})-[r:REL]->({node_id: $b}) RETURN r.conf AS c, r.neg AS n") == [
        [0.95, -0.5]
    ]


def test_carried_through_with(g):
    assert _rows(g, "MATCH ({node_id: $a})-[r:REL]->({node_id: $b}) WITH r RETURN r.conf, r.txt") == [
        [0.95, ".95"]
    ]


def test_a_leading_dot_string_stays_a_string(g):
    rows = _rows(g, "MATCH ({node_id: $a})-[r:REL]->({node_id: $b}) RETURN r.txt")
    assert rows == [[".95"]]
    assert isinstance(rows[0][0], str)


def test_a_boolean_reads_back_as_a_boolean(g):
    rows = _rows(g, "MATCH ({node_id: $a})-[r:REL]->({node_id: $b}) RETURN r.flag")
    assert rows == [[True]] and rows[0][0] is True


def test_comparisons_still_match(g):
    # The filter reads the same stored values; only the projection changed.
    assert _rows(g, "MATCH ({node_id: $a})-[r:REL]->(x) WHERE r.conf = 0.95 RETURN x.node_id") == [
        [g[1]["b"]]
    ]
    assert _rows(g, "MATCH ({node_id: $a})-[r:REL]->(x) WHERE r.conf < 0.5 RETURN x.node_id") == [
        [g[1]["c"]]
    ]
    assert _rows(g, "MATCH ({node_id: $a})-[r:REL]->(x) WHERE r.txt = '.95' RETURN x.node_id") == [
        [g[1]["b"]]
    ]


def test_ordered_grouped_and_distinct(g):
    assert _rows(g, "MATCH ({node_id: $a})-[r:REL]->() RETURN r.conf ORDER BY r.conf") == [[0.25], [0.95]]
    assert _rows(g, "MATCH ({node_id: $a})-[r:REL]->() RETURN r.conf AS c ORDER BY c DESC") == [[0.95], [0.25]]
    assert sorted(_rows(g, "MATCH ({node_id: $a})-[r:REL]->() RETURN r.conf, count(*)")) == [
        [0.25, 1],
        [0.95, 1],
    ]
    assert sorted(_rows(g, "MATCH ({node_id: $a})-[r:REL]->() RETURN DISTINCT r.conf")) == [[0.25], [0.95]]


def test_an_absent_property_is_null(g):
    assert _rows(g, "MATCH ({node_id: $a})-[r:REL]->({node_id: $b}) RETURN r.missing") == [[None]]


def test_a_node_property_between_minus_one_and_one_is_a_number(g):
    # The node side reads rdf_props.val, typed by the driver: no leading-dot text to decode.
    engine, ids, rel = g
    node = f"{ids['a']}_n"
    r = engine.execute_cypher(f"CREATE (:Rf460 {{node_id: '{node}', conf: 0.95, neg: -0.5, txt: '.95'}})")
    assert not getattr(r, "error", None), r.error
    try:
        rows = _rows(g, "MATCH (n {node_id: $n}) RETURN n.conf, n.neg, n.txt", {"n": node})
        assert rows == [[0.95, -0.5, ".95"]]
        assert isinstance(rows[0][2], str)
    finally:
        engine.delete_node(node)
