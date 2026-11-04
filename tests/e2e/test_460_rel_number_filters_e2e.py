"""4.6.0 R139: a number filter on a relationship property, and `properties(n)` on a node, keep a number's type.

Same family as R121, three shapes it left open:

* `WHERE r.c = $v` with a fraction matched nothing (and `WHERE r.c = 0.95` missed a Cypher-written edge);
* after `WITH r.c AS c`, a filter on `c` compared the text with one cast number, so `= 0.95` missed;
* `properties(n)` turned the string ".95" into the number 0.95.

The graph, written here. From a: to b by the API (`create_edge(qualifiers={"c": 0.95, "w": 5.0, "txt": ".95"})`,
stored as JSON numbers, which read back `.95` and `5`), to c by Cypher (`CREATE (a)-[:R {c: 0.95, w: 5.0}]->(c)`,
stored as the text `0.95` and `5.0`), to d by the API with c 0.25 and w 4. Worked by hand: c = 0.95 is b and c;
c = 0.25 is d; c <> 0.95 is d; w = 5 is b and c (5 = 5.0 in Cypher).

Nodes n1 (API: `properties={"txt": ".95", "c": 0.95}`) and n2 (Cypher CREATE with the same two properties):
`properties(n)` is {"txt": ".95", "c": 0.95} for each, ".95" a string and 0.95 a number.
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
    rel = f"RN139_{run}"
    ids = {k: f"rn139_{run}_{k}" for k in ("a", "b", "c", "d", "n1", "n2")}
    for k in ("a", "b", "c", "d"):
        assert engine.create_node(ids[k], labels=["Rn139"])
    assert engine.create_edge(ids["a"], rel, ids["b"], qualifiers={"c": 0.95, "w": 5.0, "txt": ".95"})
    assert engine.create_edge(ids["a"], rel, ids["d"], qualifiers={"c": 0.25, "w": 4})
    r = engine.execute_cypher(
        f"MATCH (a {{node_id: $a}}), (c {{node_id: $c}}) CREATE (a)-[:{rel} {{c: 0.95, w: 5.0}}]->(c)",
        {"a": ids["a"], "c": ids["c"]},
    )
    assert not getattr(r, "error", None), r.error
    assert engine.create_node(ids["n1"], labels=["Rn139N"], properties={"txt": ".95", "c": 0.95})
    r = engine.execute_cypher(f"CREATE (:Rn139N {{node_id: '{ids['n2']}', txt: '.95', c: 0.95}})")
    assert not getattr(r, "error", None), r.error
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield engine, ids, rel
    for nid in ids.values():
        with contextlib.suppress(Exception):
            engine.delete_node(nid)


def _ends(g, q, params=None):
    engine, ids, rel = g
    r = engine.execute_cypher(q.replace("REL", rel), dict(params or {}, a=ids["a"]))
    assert not getattr(r, "error", None), (q, r.error)
    short = {v: k for k, v in ids.items()}
    return sorted(short.get(row[0], row[0]) for row in r.rows)


@pytest.mark.parametrize("v, expected", [(0.95, ["b", "c"]), (0.25, ["d"])])
def test_a_parameter_fraction_matches_api_and_cypher_written_edges(g, v, expected):
    assert _ends(g, "MATCH ({node_id: $a})-[r:REL]->(x) WHERE r.c = $v RETURN x.node_id", {"v": v}) == expected


def test_a_literal_fraction_matches_api_and_cypher_written_edges(g):
    assert _ends(g, "MATCH ({node_id: $a})-[r:REL]->(x) WHERE r.c = 0.95 RETURN x.node_id") == ["b", "c"]


def test_not_equal(g):
    assert _ends(g, "MATCH ({node_id: $a})-[r:REL]->(x) WHERE r.c <> $v RETURN x.node_id", {"v": 0.95}) == ["d"]


def test_a_whole_number_matches_its_float_spelling(g):
    assert _ends(g, "MATCH ({node_id: $a})-[r:REL]->(x) WHERE r.w = 5 RETURN x.node_id") == ["b", "c"]


def test_a_filter_on_a_with_column_in_a_later_stage(g):
    q = "MATCH ({node_id: $a})-[r:REL]->(x) WITH r.c AS c, x WITH c, x WHERE c = 0.95 RETURN x.node_id"
    assert _ends(g, q) == ["b", "c"]
    q = "MATCH ({node_id: $a})-[r:REL]->(x) WITH r.c AS c, x WITH c, x WHERE c = $v RETURN x.node_id"
    assert _ends(g, q, {"v": 0.95}) == ["b", "c"]


def test_a_filter_on_a_with_column_pushed_into_the_match(g):
    q = "MATCH ({node_id: $a})-[r:REL]->(x) WITH r.c AS c, x WHERE c = $v RETURN x.node_id"
    assert _ends(g, q, {"v": 0.95}) == ["b", "c"]


@pytest.mark.parametrize("key", ["n1", "n2"])
def test_properties_of_a_node_keep_a_leading_dot_string_a_string(g, key):
    engine, ids, _ = g
    r = engine.execute_cypher("MATCH (n {node_id: $n}) RETURN properties(n)", {"n": ids[key]})
    assert not getattr(r, "error", None), r.error
    props = r.rows[0][0]
    props = json.loads(props) if isinstance(props, str) else props
    assert props["txt"] == ".95" and isinstance(props["txt"], str), props
    assert props["c"] == 0.95 and isinstance(props["c"], float), props
