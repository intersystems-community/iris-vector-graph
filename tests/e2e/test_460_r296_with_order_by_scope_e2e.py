"""4.6.0 R296, live: a WITH's ORDER BY on a node's own column sorts, under LIMIT, SKIP and with a MATCH, SET, MERGE
or CREATE after it; and a write whose first read fails raises instead of writing nothing.

Found by docsgate on the release head, under intersystems-irispython 5.4.0 and 5.4.1:
* `MATCH (n:KD) WITH n ORDER BY n.node_id LIMIT 2 RETURN n.id` answered no rows, SQLCODE -23 in `error`: the ORDER BY
  outside the stage's `(...) __ob` wrapper named `n0`, a table inside it. So did `id(n)`, `b.node_id` of a second node,
  and a key mixing a WITH column with one (`k + size(n.node_id)`). A SKIP without a LIMIT, or a property key (n.k),
  already sorted inside the stage and answered.
* `MATCH (n:KD) WITH n ORDER BY n.node_id CREATE (:KD2 {src: n.id})` created nothing, no exception, `error` None: the
  per-row CREATE reads the WITH's rows first, and the -23 of that read was only logged. Any failure of that read did
  the same: a Python set as `$p` (the driver's "Unsupported argument type"), and, found here, a SKIP in the WITH
  (`... SKIP 1 LIMIT 1 CREATE ...`: that read selected FROM Stage2, which does not exist, SQLCODE -30).

Worked by hand. KD nodes d1 {k: 3}, d2 {k: 1}, d3 {k: 2} (node ids d1, d2, d3); KX x1 and x3; d1 -[:KR]-> x1 and
d3 -[:KR]-> x3.
* By node id ascending d1, d2, d3; by k ascending d2 (1), d3 (2), d1 (3). `k + size(n.node_id)`: d1 5, d2 3, d3 4.
* `ORDER BY n.node_id LIMIT 2`: d1, d2 (RETURN n.id answers [['d1'], ['d2']]); DESC LIMIT 2: d3, d2; with no LIMIT
  all three; `id(n)` is the node id, so as n.node_id. DISTINCT, a value column beside the node, and a count(*)
  beside it (one row each, count 1) pick the same two. `MATCH (a:KD)-[:KR]->(b) WITH a, b ORDER BY b.node_id DESC
  LIMIT 1`: (d3, x3). `ORDER BY k + size(n.node_id) LIMIT 1`: d2. Two stages, `LIMIT 2` then `DESC LIMIT 1`: d2.
* By k: `LIMIT 2` d2, d3; `DESC LIMIT 1` d1. SKIP: `n.node_id SKIP 1` d2, d3; `SKIP 1 LIMIT 1` d2; `n.k SKIP 1
  LIMIT 1` d3; `n.node_id DESC SKIP 2` d1.
* A MATCH after it: `n.node_id LIMIT 2` keeps d1, d2, of which d1 has a KR: (d1, x1). `n.k LIMIT 2` keeps d2, d3:
  (d3, x3). `n.node_id SKIP 1`: d2, d3: (d3, x3).
* SET: `n.node_id LIMIT 2 SET n.s = 1` sets s on d1 and d2; `n.k LIMIT 1 SET n.s = 2` on d2; `n.node_id SKIP 2 SET
  n.s = 3` on d3.
* MERGE: `n.node_id LIMIT 2 MERGE (:KM {src: n.id})` two KM, src d1 and d2; with no LIMIT three; `WITH n.id AS i
  ORDER BY n.node_id LIMIT 2 MERGE (:KM {src: i})` d1 and d2.
* CREATE: `WITH n ORDER BY n.node_id CREATE (:KD2 {src: n.id})` three KD2, src d1, d2, d3. `n.node_id LIMIT 2 CREATE
  (n)-[:KR2]->(:KD2)` two KD2, one from d1 and one from d2. `n.k LIMIT 1 ... {src: n.id}` d2; `n.node_id SKIP 1
  LIMIT 1` d2; `n.k SKIP 1 LIMIT 1` d3; `n.node_id SKIP 2` d3. `WITH n.id AS i ORDER BY i SKIP 1 MERGE (:KM {src:
  i})` d2 and d3.
* `WHERE n.k = $p` with `$p` a Python set, before a per-row CREATE or MERGE: GraphWriteError, nothing written; the
  same read alone answers the driver's error in `error`.

Namespace: an IVG460F* scratch namespace (IVG_NAMESPACE); the labels are this file's own. Native on and off.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns460alt as alt

NS = os.environ.get("IVG_NAMESPACE", "")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not NS.startswith("IVG460F"), reason="IVG_NAMESPACE must name an IVG460F* scratch namespace"),
    pytest.mark.skipif(not alt.container_running(), reason=f"{alt.CONTAINER} is not running"),
]

LABELS = ("KD", "KX", "KD2", "KM")


def _q(eng, q):
    return [list(r) for r in eng.execute_cypher(q, {}, raise_on_error=True).rows]


def _run(eng, q):
    rows = _q(eng, q)
    eng.conn.commit()
    return rows


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
    for label in LABELS:
        e.execute_cypher(f"MATCH (n:{label}) DETACH DELETE n", {}, raise_on_error=True)


@pytest.fixture(params=["native", "sql"])
def eng(request, engines, conn):
    e = engines[request.param]
    _clear(e)
    e.execute_cypher(
        "CREATE (:KD {id: 'd1', k: 3}), (:KD {id: 'd2', k: 1}), (:KD {id: 'd3', k: 2})", {}, raise_on_error=True
    )
    e.execute_cypher("MATCH (a:KD {id: 'd1'}) CREATE (a)-[:KR]->(:KX {id: 'x1'})", {}, raise_on_error=True)
    e.execute_cypher("MATCH (a:KD {id: 'd3'}) CREATE (a)-[:KR]->(:KX {id: 'x3'})", {}, raise_on_error=True)
    conn.commit()
    try:
        yield e
    finally:
        _clear(e)
        conn.commit()


def test_the_reported_read(eng):
    assert _q(eng, "MATCH (n:KD) WITH n ORDER BY n.node_id LIMIT 2 RETURN n.id") == [["d1"], ["d2"]]


@pytest.mark.parametrize("q,want", [
    ("MATCH (n:KD) WITH n ORDER BY n.node_id DESC LIMIT 2 RETURN n.id", [["d2"], ["d3"]]),
    ("MATCH (n:KD) WITH n ORDER BY n.node_id RETURN n.id", [["d1"], ["d2"], ["d3"]]),
    ("MATCH (n:KD) WITH n ORDER BY id(n) LIMIT 2 RETURN n.id", [["d1"], ["d2"]]),
    ("MATCH (n:KD) WITH DISTINCT n ORDER BY n.node_id LIMIT 2 RETURN n.id", [["d1"], ["d2"]]),
    ("MATCH (n:KD) WITH n.id AS i, n ORDER BY n.node_id LIMIT 2 RETURN i", [["d1"], ["d2"]]),
    ("MATCH (n:KD) WITH n, count(*) AS c ORDER BY n.node_id LIMIT 2 RETURN n.id, c", [["d1", 1], ["d2", 1]]),
    ("MATCH (a:KD)-[:KR]->(b) WITH a, b ORDER BY b.node_id DESC LIMIT 1 RETURN a.id, b.id", [["d3", "x3"]]),
    ("MATCH (n:KD) WITH n, n.k AS k ORDER BY k + size(n.node_id) LIMIT 1 RETURN n.id", [["d2"]]),
    ("MATCH (n:KD) WITH n ORDER BY n.node_id LIMIT 2 WITH n ORDER BY n.node_id DESC LIMIT 1 RETURN n.id", [["d2"]]),
    # on a property
    ("MATCH (n:KD) WITH n ORDER BY n.k LIMIT 2 RETURN n.id", [["d2"], ["d3"]]),
    ("MATCH (n:KD) WITH n ORDER BY n.k DESC LIMIT 1 RETURN n.id", [["d1"]]),
    # with SKIP
    ("MATCH (n:KD) WITH n ORDER BY n.node_id SKIP 1 RETURN n.id", [["d2"], ["d3"]]),
    ("MATCH (n:KD) WITH n ORDER BY n.node_id SKIP 1 LIMIT 1 RETURN n.id", [["d2"]]),
    ("MATCH (n:KD) WITH n ORDER BY n.k SKIP 1 LIMIT 1 RETURN n.id", [["d3"]]),
    ("MATCH (n:KD) WITH n ORDER BY n.node_id DESC SKIP 2 RETURN n.id", [["d1"]]),
    # followed by a MATCH
    ("MATCH (n:KD) WITH n ORDER BY n.node_id LIMIT 2 MATCH (n)-[:KR]->(m) RETURN n.id, m.id", [["d1", "x1"]]),
    ("MATCH (n:KD) WITH n ORDER BY n.k LIMIT 2 MATCH (n)-[:KR]->(m) RETURN n.id, m.id", [["d3", "x3"]]),
    ("MATCH (n:KD) WITH n ORDER BY n.node_id SKIP 1 MATCH (n)-[:KR]->(m) RETURN n.id, m.id", [["d3", "x3"]]),
])
def test_the_rows_the_order_picks(eng, q, want):
    assert sorted(_q(eng, q)) == want


@pytest.mark.parametrize("q,want", [
    ("MATCH (n:KD) WITH n ORDER BY n.node_id LIMIT 2 SET n.s = 1", [["d1", 1], ["d2", 1], ["d3", None]]),
    ("MATCH (n:KD) WITH n ORDER BY n.k LIMIT 1 SET n.s = 2", [["d1", None], ["d2", 2], ["d3", None]]),
    ("MATCH (n:KD) WITH n ORDER BY n.node_id SKIP 2 SET n.s = 3", [["d1", None], ["d2", None], ["d3", 3]]),
])
def test_a_set_after_it(eng, q, want):
    _run(eng, q)
    assert sorted(_q(eng, "MATCH (n:KD) RETURN n.id, n.s")) == want


@pytest.mark.parametrize("q,want", [
    ("MATCH (n:KD) WITH n ORDER BY n.node_id LIMIT 2 MERGE (:KM {src: n.id})", [["d1"], ["d2"]]),
    ("MATCH (n:KD) WITH n ORDER BY n.node_id MERGE (:KM {src: n.id})", [["d1"], ["d2"], ["d3"]]),
    ("MATCH (n:KD) WITH n.id AS i ORDER BY n.node_id LIMIT 2 MERGE (:KM {src: i})", [["d1"], ["d2"]]),
])
def test_a_merge_after_it(eng, q, want):
    _run(eng, q)
    assert sorted(_q(eng, "MATCH (m:KM) RETURN m.src")) == want


def test_the_reported_create(eng):
    _run(eng, "MATCH (n:KD) WITH n ORDER BY n.node_id CREATE (:KD2 {src: n.id})")
    assert sorted(_q(eng, "MATCH (m:KD2) RETURN m.src")) == [["d1"], ["d2"], ["d3"]]


@pytest.mark.parametrize("q,want", [
    ("MATCH (n:KD) WITH n ORDER BY n.k LIMIT 1 CREATE (:KD2 {src: n.id})", [["d2"]]),
    ("MATCH (n:KD) WITH n ORDER BY n.node_id SKIP 1 LIMIT 1 CREATE (:KD2 {src: n.id})", [["d2"]]),
    ("MATCH (n:KD) WITH n ORDER BY n.k SKIP 1 LIMIT 1 CREATE (:KD2 {src: n.id})", [["d3"]]),
    ("MATCH (n:KD) WITH n ORDER BY n.node_id SKIP 2 CREATE (:KD2 {src: n.id})", [["d3"]]),
])
def test_a_create_after_it(eng, q, want):
    _run(eng, q)
    assert sorted(_q(eng, "MATCH (m:KD2) RETURN m.src")) == want


def test_a_merge_of_the_with_s_values_after_skip(eng):
    _run(eng, "MATCH (n:KD) WITH n.id AS i ORDER BY i SKIP 1 MERGE (:KM {src: i})")
    assert sorted(_q(eng, "MATCH (m:KM) RETURN m.src")) == [["d2"], ["d3"]]


@pytest.mark.parametrize("q,label", [
    ("MATCH (n:KD) WHERE n.k = $p WITH n CREATE (:KD2 {src: n.id})", "KD2"),
    ("MATCH (n:KD) WHERE n.k = $p WITH n.id AS i MERGE (:KM {src: i})", "KM"),
])
@pytest.mark.parametrize("raise_on_error", [False, True])
def test_a_write_whose_first_read_fails_raises(eng, q, label, raise_on_error):
    """The driver refuses a Python set as a parameter ("Unsupported argument type"). A read answers that in `error`;
    these writes read the WITH's rows first, and wrote nothing with `error` None."""
    from iris_vector_graph import GraphWriteError

    with pytest.raises(GraphWriteError, match="nothing written.*Unsupported argument type"):
        eng.execute_cypher(q, {"p": {1, 2}}, raise_on_error=raise_on_error)
    eng.conn.rollback()
    assert _q(eng, f"MATCH (m:{label}) RETURN count(m)") == [[0]]
    # the same read on its own keeps its contract: the error in `error`, no exception
    r = eng.execute_cypher("MATCH (n:KD) WHERE n.k = $p RETURN n.id", {"p": {1, 2}})
    assert r.rows == [] and "Unsupported argument type" in str(r.error)


def test_a_create_linking_from_the_carried_node(eng):
    _run(eng, "MATCH (n:KD) WITH n ORDER BY n.node_id LIMIT 2 CREATE (n)-[:KR2]->(:KD2)")
    assert sorted(_q(eng, "MATCH (n:KD)-[:KR2]->(m:KD2) RETURN n.id, count(m)")) == [["d1", 1], ["d2", 1]]
    assert _q(eng, "MATCH (m:KD2) RETURN count(m)") == [[2]]
