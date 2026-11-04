"""4.6.0 R273 (native grouping by a whole float), live: a group key that is a stored whole float keeps its point on the
native path when the groups are merged from workers and when the key is long.

R263 made the native executor write a $DOUBLE in a row as CY_TYPED marks it, so `RETURN n.g` of a stored 5.0 answers
5.0. Found by fixes35: a group's key still came back 5 in two cases. A long key (400 bytes or more of values) was
kept as its JSON, which writes a whole $DOUBLE as an integer; and a parallel run's workers handed their groups back
by that JSON. Each group is now kept as its values, as a short one in one process was.

Graph r273k, written by Cypher: (a:K {g: 5.0, lg: <400 x's>}), (b:K {g: 2.0}), (c:K {g: 5.0, lg: <400 x's>}),
a-[:R]->b, a-[:R]->c. Worked by hand:
* `MATCH (n:K) RETURN n.g, count(*) ORDER BY n.g`: (2.0, 1), (5.0, 2);
* with the long key, `RETURN n.g, n.lg, count(*) ORDER BY n.g`: (2.0, null, 1), (5.0, <400 x's>, 2);
* over a hop, `MATCH (n:K)-[:R]->(m) RETURN m.g, count(*) ORDER BY m.g`: (2.0, 1), (5.0, 1).
Each in one process and split over workers (native_parallel_min 0), where the run must have been parallel; the SQL
path gives the same answers.

Namespace: an IVG460F* scratch namespace with this checkout's classes. Under intersystems-irispython 5.4.0 and 5.4.1.
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

G = "r273k"
U = f"USE GRAPH '{G}' "
L = "x" * 400

CASES = [
    ("a short key", "MATCH (n:K) RETURN n.g, count(*) ORDER BY n.g", [[2.0, 1], [5.0, 2]]),
    ("a long key", "MATCH (n:K) RETURN n.g, n.lg, count(*) ORDER BY n.g", [[2.0, None, 1], [5.0, L, 2]]),
    ("over a hop", "MATCH (n:K)-[:R]->(m) RETURN m.g, count(*) ORDER BY m.g", [[2.0, 1], [5.0, 1]]),
]


@pytest.fixture(scope="module")
def engines():
    import iris

    from iris_vector_graph import IRISGraphEngine

    conn = iris.connect("localhost", alt.PORT, NS, "_SYSTEM", "SYS")
    nat = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=True)
    nat.initialize_schema()
    nat.erase_graph(G)
    nat.execute_cypher(U + "CREATE (a:K {g: 5.0, lg: $lg}), (b:K {g: 2.0}), (c:K {g: 5.0, lg: $lg}), "
                       "(a)-[:R]->(b), (a)-[:R]->(c)", {"lg": L}, raise_on_error=True)
    conn.commit()
    nat._native_privileges_cached = True
    nat.native_route_ms = 0
    sql = IRISGraphEngine(conn, namespace=NS, embedding_dimension=768, native_cypher=False)
    try:
        yield {"native": nat, "sql": sql}
    finally:
        nat.erase_graph(G)
        conn.close()


def _typed(rows):
    return [[(type(v).__name__, v) for v in r] for r in rows]


@pytest.mark.parametrize("split", [False, True], ids=["one process", "workers"])
@pytest.mark.parametrize("name,query,want", CASES, ids=[c[0] for c in CASES])
def test_a_native_group_key_keeps_a_whole_float(engines, split, name, query, want):
    eng = engines["native"]
    saved = eng.native_parallel_min
    eng.native_parallel_min = 0 if split else -1
    answers = eng.__dict__.get("_native_answers", 0)
    runs = eng.__dict__.get("_native_parallel_runs", 0)
    try:
        r = eng.execute_cypher(U + query, {}, raise_on_error=True)
    finally:
        eng.native_parallel_min = saved
    assert getattr(r, "error", None) is None, r.error
    assert eng.__dict__.get("_native_answers", 0) == answers + 1, "the native path did not answer"
    assert (eng.__dict__.get("_native_parallel_runs", 0) == runs + 1) is split
    assert _typed(r.rows) == _typed(want)


@pytest.mark.parametrize("name,query,want", CASES, ids=[c[0] for c in CASES])
def test_the_sql_path_agrees(engines, name, query, want):
    r = engines["sql"].execute_cypher(U + query, {}, raise_on_error=True)
    assert _typed(r.rows) == _typed(want)
