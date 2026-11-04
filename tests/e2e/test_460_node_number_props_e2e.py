"""4.6.0 R168: a node property holding the number 0.95 is found by a number filter, whichever writer stored it.

`rdf_props.val` is VARCHAR. `create_node`, `bulk_create_nodes` and `store_node` write `str(v)` (the text `0.95`), and a
Cypher CREATE or SET binds the float, which IRIS keeps as a `$DOUBLE` (text `.94999999999999995559`). `WHERE n.score =
0.95` compared the column with the text `.95`, and `= $v` or `{score: 0.95}` with a double, so each missed one of the
two. The filters now compare (IN every spelling OR = CAST(.. AS DOUBLE)). The stored forms are unchanged: what
`RETURN n.score` answers is what 4.5.x answers (the string '0.95' for an API-written number, the float for a
Cypher-written one), pinned here so a change to it is seen.

The graph, worked by hand (label L168, ids unique to the run):
- score 0.95, the number, on n_api (create_node), n_bulk (bulk_create_nodes), n_store (store_node), n_cy (Cypher
  CREATE), n_set (Cypher SET n.score = 0.95) and n_param (SET n.score = $v, v = 0.95);
- score 0.25, the number, on z_api (create_node) and z_cy (Cypher CREATE);
- txt, a string: ".95" on z_api and "0.95" on z_cy (a string that spells the number).

So `n.score = 0.95`, `= $v`, `{score: 0.95}` and `{score: $v}` answer the six n_* nodes; `n.score <> 0.95` answers
z_api and z_cy. `RETURN n.score` is the string '0.95' for the API's n_api, n_bulk, n_store and the float 0.95 for
the Cypher-written n_cy, n_set, n_param ('0.25' and 0.25 for z_api and z_cy); `RETURN n.txt` is the string ".95" and the
string "0.95". A string compared with a number is the KNOWN_ISSUES entry, not checked here.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

NUMBERED = ("n_api", "n_bulk", "n_store", "n_cy", "n_set", "n_param")
OTHERS = ("z_api", "z_cy")


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    ids = {k: f"r168_{run}_{k}" for k in NUMBERED + OTHERS}
    assert e.create_node(ids["n_api"], labels=["L168"], properties={"score": 0.95})
    assert e.bulk_create_nodes([{"id": ids["n_bulk"], "labels": ["L168"], "properties": {"score": 0.95}}])
    # store_node writes no `id` property of its own (create_node and bulk_create_nodes do), and the reads key on it
    assert e.store_node(ids["n_store"], properties={"id": ids["n_store"], "score": 0.95}, labels=["L168"])
    assert e.create_node(ids["z_api"], labels=["L168"], properties={"score": 0.25, "txt": ".95"})
    for q, params in (
        ("CREATE (:L168 {id: $id, score: 0.95})", {"id": ids["n_cy"]}),
        ("CREATE (:L168 {id: $id, score: 0.25, txt: '0.95'})", {"id": ids["z_cy"]}),
        ("CREATE (:L168 {id: $id})", {"id": ids["n_set"]}),
        ("MATCH (n:L168 {id: $id}) SET n.score = 0.95", {"id": ids["n_set"]}),
        ("CREATE (:L168 {id: $id})", {"id": ids["n_param"]}),
        ("MATCH (n:L168 {id: $id}) SET n.score = $v", {"id": ids["n_param"], "v": 0.95}),
    ):
        r = e.execute_cypher(q, params)
        assert r.error is None, (q, r.error)
    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield e, ids
    for nid in ids.values():
        e.delete_node(nid)


def _ids(g, query, params=None):
    e, ids = g
    r = e.execute_cypher(query, dict(params or {}))
    assert r.error is None, (query, r.error)
    by_id = {v: k for k, v in ids.items()}
    return sorted(by_id[row[0]] for row in r.rows if row[0] in by_id)


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n:L168) WHERE n.score = 0.95 RETURN n.id",
        "MATCH (n:L168) WHERE n.score = $v RETURN n.id",
        "MATCH (n:L168 {score: 0.95}) RETURN n.id",
        "MATCH (n:L168 {score: $v}) RETURN n.id",
    ],
    ids=["where_literal", "where_param", "inline_literal", "inline_param"],
)
def test_a_number_filter_finds_every_writer_s_number(g, query):
    assert _ids(g, query, {"v": 0.95}) == sorted(NUMBERED)


def test_not_equal_leaves_the_number_out(g):
    assert _ids(g, "MATCH (n:L168) WHERE n.score <> $v RETURN n.id", {"v": 0.95}) == sorted(OTHERS)


def test_return_answers_what_4_5_answers(g):
    e, ids = g
    r = e.execute_cypher("MATCH (n:L168) RETURN n.id, n.score")
    assert r.error is None, r.error
    got = {row[0]: row[1] for row in r.rows}
    want = {ids[k]: "0.95" for k in ("n_api", "n_bulk", "n_store")}
    want |= {ids[k]: 0.95 for k in ("n_cy", "n_set", "n_param")}
    want |= {ids["z_api"]: "0.25", ids["z_cy"]: 0.25}
    typed = {nid: (type(got.get(nid)).__name__, got.get(nid)) for nid in want}
    assert typed == {nid: (type(v).__name__, v) for nid, v in want.items()}


def test_return_keeps_a_string_a_string(g):
    e, ids = g
    r = e.execute_cypher("MATCH (n:L168) WHERE n.txt IS NOT NULL RETURN n.id, n.txt")
    assert r.error is None, r.error
    got = {row[0]: row[1] for row in r.rows}
    assert {ids["z_api"]: got.get(ids["z_api"]), ids["z_cy"]: got.get(ids["z_cy"])} == {
        ids["z_api"]: ".95",
        ids["z_cy"]: "0.95",
    }
    assert all(isinstance(v, str) for v in got.values()), got
