"""4.6.0 R292 (the CREATE items), live: a CREATE writes computed values with their type, keeps a stored string a
string through a WITH, and runs once per row when a WITH carries the node it links from.

Found by fixes43 on the release head, under intersystems-irispython 5.4.0 and 5.4.1:
* `MATCH (n) CREATE (:H {v: n.k * 2})` (the per-row statements, R189) wrote the arithmetic through
  CAST(... AS VARCHAR(4000)): 6 read back as '6' and 3.0 as '3';
* `MATCH (n) WITH n.k AS k CREATE (:H {v: k})` (R120's per-row plan) re-typed each value from text, so a stored string
  '7' was written as the integer 7;
* `MATCH (n) WITH n CREATE (n)-[:R]->(:H)` made one H for every row: a stage's rows carry no row identity
  (_create_row_key), so the static statements make one node, and with `{v: n.k}` they bound the AST as a parameter
  ("Unsupported argument type") and wrote nothing.

Worked by hand. N292 nodes {k: 3}, {k: 1.5}; C292 nodes {k: 2.5}, {k: 2}, {k: '7'}, {k: true}, {name: 'nok'} (no k).
* `MATCH (n:N292) CREATE (:H292 {v: n.k * 2})`: 6 (an integer) and 3.0 (a float). With `{v: n.k + 1, w: n.k - 1,
  x: n.k / 2}`: for 3, 4, 2 and 1 (integer division); for 1.5, 2.5, 0.5 and 0.75. `CREATE (h:H292 {v: n.k * 2})
  RETURN h.v` answers 6 and 3.0.
* `MATCH (n:C292) WITH n.k AS k CREATE (:H292 {v: k})`: five H292 nodes, v 2.5, 2, '7' (a string), true, and one with
  no v (null is no property). Renamed again (`WITH k AS j`) the same.
* `MATCH (n:N292) WITH n CREATE (n)-[:R292]->(:H292)`: two H292 nodes, one from each N292.
  With `{v: n.k}` each H292 holds its N292's k: (1.5, 1.5), (3, 3); with `WITH n, n.k * 2 AS d ... {v: d}`: (1.5, 3.0),
  (3, 6). With `RETURN n.k, h.v` the CREATE answers those pairs.
* `MATCH (n:N292), (m:N292) WITH n CREATE (n)-[:R292]->(:H292)`: four rows (two n for each m), so four H292 nodes, two
  from each N292.
* Under USE GRAPH 'g292', which holds G292 {k: 1}, {k: 2}: `WITH n CREATE (n)-[:R292]->(:H292 {v: n.k})` makes two
  H292 nodes in g292 linked from (1, 1) and (2, 2); no H292 row has another graph.

Data already stored keeps its form: these are new nodes, written in the form their values have.

Namespace: an IVG460F* scratch namespace; the labels are this file's own. Native on and off.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460F"), reason="IVG_NAMESPACE must name an IVG460F* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

LABELS = ("N292", "C292", "G292", "H292")


def _typed(v):
    if isinstance(v, str) and v[:1] == "[":
        v = json.loads(v)
    if isinstance(v, list):
        return ["list", [_typed(x) for x in v]]
    return [type(v).__name__, v]


def _key(t):
    return json.dumps(t, sort_keys=True)


def _rows(rows):
    return sorted(([_typed(v) for v in r] for r in rows), key=_key)


def _q(eng, q, graph=None):
    use = f"USE GRAPH '{graph}' " if graph else ""
    return eng.execute_cypher(use + q, {}, raise_on_error=True).rows


def _values(eng, prop="v", graph=None):
    return _rows(_q(eng, f"MATCH (h:H292) RETURN h.{prop}", graph))


def _pairs(eng, graph=None):
    return _rows(_q(eng, "MATCH (n)-[:R292]->(h:H292) RETURN n.k, h.v", graph))


@pytest.fixture(scope="module")
def conn():
    import iris

    c = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    try:
        yield c
    finally:
        c.close()


@pytest.fixture(scope="module")
def engines(conn):
    from iris_vector_graph import IRISGraphEngine

    out = {}
    for path in ("native", "sql"):
        e = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=path == "native")
        e._native_privileges_cached = True
        e.native_route_ms = 0
        out[path] = e
    out["native"].initialize_schema()
    return out


def _clear(e):
    e.erase_graph("g292")
    for label in LABELS:
        e.execute_cypher(f"MATCH (n:{label}) DETACH DELETE n", {}, raise_on_error=True)


@pytest.fixture(params=["native", "sql"])
def eng(request, engines, conn):
    e = engines[request.param]
    _clear(e)
    e.execute_cypher(
        "CREATE (:N292 {k: 3}), (:N292 {k: 1.5}), (:C292 {k: 2.5}), (:C292 {k: 2}), (:C292 {k: '7'}), "
        "(:C292 {k: true}), (:C292 {name: 'nok'})", {}, raise_on_error=True,
    )
    e.execute_cypher("USE GRAPH 'g292' CREATE (:G292 {k: 1}), (:G292 {k: 2})", {}, raise_on_error=True)
    conn.commit()
    try:
        yield e
    finally:
        _clear(e)
        conn.commit()


def _run(eng, q, graph=None):
    rows = _q(eng, q, graph)
    eng.conn.commit()
    return rows


def test_arithmetic_is_written_as_a_number(eng):
    _run(eng, "MATCH (n:N292) CREATE (:H292 {v: n.k * 2})")
    assert _values(eng) == _rows([[6], [3.0]])


def test_each_operator_keeps_the_row_s_type(eng):
    _run(eng, "MATCH (n:N292) CREATE (:H292 {v: n.k + 1, w: n.k - 1, x: n.k / 2})")
    got = _rows(_q(eng, "MATCH (h:H292) RETURN h.v, h.w, h.x"))
    assert got == _rows([[4, 2, 1], [2.5, 0.5, 0.75]])


def test_a_return_of_the_written_arithmetic(eng):
    rows = _run(eng, "MATCH (n:N292) CREATE (h:H292 {v: n.k * 2}) RETURN h.v")
    assert _rows(rows) == _rows([[6], [3.0]])


def test_a_with_column_of_a_stored_string_stays_a_string(eng):
    _run(eng, "MATCH (n:C292) WITH n.k AS k CREATE (:H292 {v: k})")
    assert _values(eng) == _rows([[2.5], [2], ["7"], [True], [None]])


def test_renamed_again(eng):
    _run(eng, "MATCH (n:C292) WITH n.k AS k WITH k AS j CREATE (:H292 {v: j})")
    assert _values(eng) == _rows([[2.5], [2], ["7"], [True], [None]])


def test_a_with_carrying_the_start_node_creates_once_per_row(eng):
    _run(eng, "MATCH (n:N292) WITH n CREATE (n)-[:R292]->(:H292)")
    assert _q(eng, "MATCH (h:H292) RETURN count(h)") == [[2]]
    assert _rows(_q(eng, "MATCH (n:N292)-[:R292]->(h:H292) RETURN n.k, count(h)")) == _rows([[3, 1], [1.5, 1]])


def test_the_new_node_reads_the_carried_node_s_property(eng):
    _run(eng, "MATCH (n:N292) WITH n CREATE (n)-[:R292]->(:H292 {v: n.k})")
    assert _pairs(eng) == _rows([[3, 3], [1.5, 1.5]])


def test_a_value_column_beside_the_node(eng):
    _run(eng, "MATCH (n:N292) WITH n, n.k * 2 AS d CREATE (n)-[:R292]->(:H292 {v: d})")
    assert _pairs(eng) == _rows([[3, 6], [1.5, 3.0]])


def test_the_create_returns_its_rows(eng):
    rows = _run(eng, "MATCH (n:N292) WITH n CREATE (n)-[:R292]->(h:H292 {v: n.k}) RETURN n.k, h.v")
    assert _rows(rows) == _rows([[3, 3], [1.5, 1.5]])
    assert _pairs(eng) == _rows([[3, 3], [1.5, 1.5]])


def test_a_repeated_node_is_one_row_each_time(eng):
    _run(eng, "MATCH (n:N292), (m:N292) WITH n CREATE (n)-[:R292]->(:H292)")
    assert _q(eng, "MATCH (h:H292) RETURN count(h)") == [[4]]
    assert _rows(_q(eng, "MATCH (n:N292)-[:R292]->(h:H292) RETURN n.k, count(h)")) == _rows([[3, 2], [1.5, 2]])


def test_under_use_graph(eng):
    _run(eng, "MATCH (n:G292) WITH n CREATE (n)-[:R292]->(:H292 {v: n.k})", "g292")
    assert _pairs(eng, "g292") == _rows([[1, 1], [2, 2]])
    cur = eng.conn.cursor()
    cur.execute("SELECT COALESCE(graph_id, ''), COUNT(*) FROM Graph_KG.rdf_labels WHERE label = 'H292' "
                "GROUP BY COALESCE(graph_id, '')")
    assert [tuple(r) for r in cur.fetchall()] == [("g292", 2)]
    cur.close()
