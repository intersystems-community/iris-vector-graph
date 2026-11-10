"""4.6.0 R168: a node property holding the number 0.95 is found by a number filter, whichever writer stored it.

`rdf_props.val` is VARCHAR. Before 4.6.4 `create_node`, `bulk_create_nodes` and `store_node` wrote `str(v)` (the text
`0.95`), and a Cypher CREATE or SET binds the float, which IRIS keeps as a `$DOUBLE` (text `.94999999999999995559`). `WHERE n.score =
0.95` compared the column with the text `.95`, and `= $v` or `{score: 0.95}` with a double, so each missed one of the
two. The filters now compare (IN every spelling OR = CAST(.. AS DOUBLE)). From 4.6.4 the API binds the float too
(test_464_api_typed_props_e2e), so `RETURN n.score` answers the float for every writer; n_old holds the text 4.6.3's
create_node stored, which a database keeps, and still reads as the string '0.95'. Pinned here so a change is seen.

The graph, worked by hand (label L168, ids unique to the run):
- score 0.95, the number, on n_api (create_node), n_bulk (bulk_create_nodes), n_store (store_node), n_cy (Cypher
  CREATE), n_set (Cypher SET n.score = 0.95) and n_param (SET n.score = $v, v = 0.95); and the text '0.95' on n_old
  (create_node of the string, what 4.6.3's create_node stored for the number);
- score 0.25, the number, on z_api (create_node) and z_cy (Cypher CREATE);
- txt, a string: ".95" on z_api and "0.95" on z_cy (a string that spells the number).

So `n.score = 0.95`, `= $v`, `{score: 0.95}` and `{score: $v}` answer the six n_* nodes a number was written to;
`n.score <> 0.95` answers z_api, z_cy and n_old. 4.7.0 row 24: a number filter reads the row's type, so n_old's text
(a string, as `RETURN n.score` reads it) is not 0.95 any more; before, every n_* node matched and n_old not `<>`. `RETURN n.score` is the float 0.95 for the six n_* nodes a number was written to and the string '0.95'
for n_old (0.25 for z_api and z_cy); `RETURN n.txt` is the string ".95" and the
string "0.95". A string compared with a number is the KNOWN_ISSUES entry, not checked here.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

pytestmark = [pytest.mark.e2e]

NUMBERED = ("n_api", "n_bulk", "n_store", "n_cy", "n_set", "n_param", "n_old")
NUMBERS = NUMBERED[:-1]  # n_old holds 4.6.3's text, a string to a filter since 4.7.0 (row 24)
OTHERS = ("z_api", "z_cy")


@pytest.fixture(scope="module")
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    run = uuid.uuid4().hex[:8]
    ids = {k: f"r168_{run}_{k}" for k in NUMBERED + OTHERS}
    assert e.create_node(ids["n_api"], labels=["L168"], properties={"score": 0.95})
    assert e.bulk_create_nodes([{"id": ids["n_bulk"], "labels": ["L168"], "properties": {"score": 0.95}}])
    # an explicit `id`, as before R181 (store_node now writes one of its own when none is given)
    assert e.store_node(ids["n_store"], properties={"id": ids["n_store"], "score": 0.95}, labels=["L168"])
    assert e.create_node(ids["z_api"], labels=["L168"], properties={"score": 0.25, "txt": ".95"})
    assert e.create_node(ids["n_old"], labels=["L168"], properties={"score": "0.95"})  # 4.6.3's stored form of 0.95
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
    assert _ids(g, query, {"v": 0.95}) == sorted(NUMBERS)


def test_not_equal_leaves_the_number_out(g):
    assert _ids(g, "MATCH (n:L168) WHERE n.score <> $v RETURN n.id", {"v": 0.95}) == sorted(OTHERS + ("n_old",))


def test_return_answers_the_written_type(g):
    e, ids = g
    r = e.execute_cypher("MATCH (n:L168) RETURN n.id, n.score")
    assert r.error is None, r.error
    got = {row[0]: row[1] for row in r.rows}
    want = {ids[k]: 0.95 for k in ("n_api", "n_bulk", "n_store", "n_cy", "n_set", "n_param")}
    want |= {ids["n_old"]: "0.95", ids["z_api"]: 0.25, ids["z_cy"]: 0.25}
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
