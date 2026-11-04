"""4.6.0 R121: relationship-property defects found while fixing R093.

Each test writes its edge's properties itself; the expected answer is the value written.

* a user property named `s`, `p` or `o_id` answered the edge's source, type or target column;
* `{c: 0.95}` or `{c: $v}` as an inline relationship filter matched nothing;
* `properties(r)` turned the string ".95" into the number 0.95;
* `WITH r.c AS c RETURN c` answered the text '.95' for the number 0.95;
* an inline relationship property `{f: true}` written by CREATE was stored as NULL.
"""

from __future__ import annotations

import contextlib
import json
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    rel = f"RD121_{run}"
    ids = {k: f"rd121_{run}_{k}" for k in ("a", "b", "c")}
    for nid in ids.values():
        engine.create_node(nid, labels=["Rd121"])
    engine.create_edge(
        ids["a"], rel, ids["b"], qualifiers={"s": "user-s", "p": "user-p", "o_id": "user-o", "c": 0.95, "txt": ".95"}
    )
    engine.create_edge(ids["a"], rel, ids["c"], qualifiers={"c": 0.25, "txt": "x"})
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield engine, ids, rel
    for nid in list(ids.values()) + [f"{ids['a']}_x", f"{ids['a']}_y", f"{ids['a']}_z"]:
        with contextlib.suppress(Exception):
            engine.delete_node(nid)


def _rows(g, q, params=None):
    engine, ids, rel = g
    r = engine.execute_cypher(q.replace("REL", rel), dict(params or {}, a=ids["a"], b=ids["b"]))
    assert not getattr(r, "error", None), (q, r.error)
    return r.rows


def test_a_property_named_like_an_edge_column_reads_the_property(g):
    rows = _rows(g, "MATCH ({node_id: $a})-[r:REL]->({node_id: $b}) RETURN r.s, r.p, r.o_id")
    assert rows == [["user-s", "user-p", "user-o"]]


def test_a_property_named_like_an_edge_column_in_a_filter(g):
    rows = _rows(g, "MATCH ({node_id: $a})-[r:REL]->(x) WHERE r.p = 'user-p' RETURN x.node_id")
    assert rows == [[g[1]["b"]]]


def test_an_inline_fraction_filter_matches(g):
    assert _rows(g, "MATCH ({node_id: $a})-[r:REL {c: 0.95}]->(x) RETURN x.node_id") == [[g[1]["b"]]]


def test_an_inline_parameter_fraction_filter_matches(g):
    assert _rows(g, "MATCH ({node_id: $a})-[r:REL {c: $v}]->(x) RETURN x.node_id", {"v": 0.95}) == [[g[1]["b"]]]
    assert _rows(g, "MATCH ({node_id: $a})-[r:REL {c: $v}]->(x) RETURN x.node_id", {"v": 0.25}) == [[g[1]["c"]]]


def test_an_inline_string_filter_still_matches(g):
    assert _rows(g, "MATCH ({node_id: $a})-[r:REL {txt: '.95'}]->(x) RETURN x.node_id") == [[g[1]["b"]]]


def test_properties_keeps_a_leading_dot_string_a_string(g):
    rows = _rows(g, "MATCH ({node_id: $a})-[r:REL]->({node_id: $b}) RETURN properties(r)")
    props = rows[0][0]
    props = json.loads(props) if isinstance(props, str) else props  # a map arrives as its JSON text
    assert props["txt"] == ".95" and isinstance(props["txt"], str), props
    assert props["c"] == 0.95, props


def test_a_property_carried_by_with_is_a_number(g):
    rows = _rows(g, "MATCH ({node_id: $a})-[r:REL]->({node_id: $b}) WITH r.c AS c RETURN c")
    assert rows == [[0.95]]
    rows = _rows(g, "MATCH ({node_id: $a})-[r:REL]->({node_id: $b}) WITH r.c AS c, r.txt AS t RETURN c, t")
    assert rows == [[0.95, ".95"]]


def test_an_inline_boolean_relationship_property_is_stored(g):
    engine, ids, rel = g
    x, y = f"{ids['a']}_x", f"{ids['a']}_y"
    r = engine.execute_cypher(
        f"CREATE (:Rd121 {{node_id: '{x}'}})-[:{rel} {{f: true, g: false, n: 0.5}}]->(:Rd121 {{node_id: '{y}'}})"
    )
    assert not getattr(r, "error", None), r.error
    rows = _rows(g, "MATCH ({node_id: $x})-[r:REL]->() RETURN r.f, r.g, r.n", {"x": x})
    assert rows == [[True, False, 0.5]]
    assert rows[0][0] is True and rows[0][1] is False


@pytest.mark.parametrize("verb", ["CREATE", "MERGE"])
def test_an_inline_relationship_property_between_matched_nodes_is_stored(g, verb):
    engine, ids, rel = g
    rel2 = f"{rel}_{verb}"
    r = engine.execute_cypher(
        f"MATCH (a {{node_id: $a}}), (b {{node_id: $b}}) {verb} (a)-[:{rel2} {{f: true, n: 0.5, t: 'x'}}]->(b)",
        {"a": ids["a"], "b": ids["b"]},
    )
    assert not getattr(r, "error", None), r.error
    rows = _rows(g, f"MATCH ({{node_id: $a}})-[r:{rel2}]->() RETURN r.f, r.n, r.t")
    assert rows == [[True, 0.5, "x"]]


def test_an_inline_relationship_property_to_a_created_node_is_stored(g):
    engine, ids, rel = g
    rel2 = f"{rel}_NEW"
    z = f"{ids['a']}_z"
    r = engine.execute_cypher(
        f"MATCH (a {{node_id: $a}}) CREATE (a)-[:{rel2} {{f: true}}]->(:Rd121 {{node_id: '{z}'}})", {"a": ids["a"]}
    )
    assert not getattr(r, "error", None), r.error
    assert _rows(g, f"MATCH ({{node_id: $a}})-[r:{rel2}]->() RETURN r.f") == [[True]]
