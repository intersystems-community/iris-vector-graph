"""4.6.0 R200, live: after setup `Graph_KG.kg_RRF_FUSE` exists and fuses, on Community 2026.1 as on 2026.3.

On the compose image (iris-community:2026.1) its CREATE failed at Prepare, `SQLCODE -1 ... SELECT expected, <end of
statement> found ^ WITH V AS (`: 2026.1 rejects a multi-line WITH in a procedure body. Setup logged it at debug level
and the procedure was simply absent. The body now joins derived tables instead of CTEs.

Worked by hand, in the graph r200:g of this namespace's default route (rows inserted straight into
`kg_NodeEmbeddings` and `docs` under that graph_id, as tests/e2e/test_230_retrieval_scope.py does: the procedure's
FROM is fixed at the default tables). Width d is whatever `kg_NodeEmbeddings.emb` declares; e1, e2 the first two axes.

    node  vector               doc                             cosine to q = e1   occurrences of r200token
    a     e1                   "r200token once"                1.0  (rank 1)      1 (rank 2)
    b     0.6 e1 + 0.8 e2      none                            0.6  (rank 2)      -
    c     e2                   "r200token r200token r200token" 0.0  (rank 3)      3 (rank 1)

With c = 60 and a missing rank as 1000000000: rrf(a) = 1/61 + 1/62, rrf(c) = 1/63 + 1/61, rrf(b) = 1/62 + 1/(60 +
1e9). Order a, c, b; vs 1.0, 0.0, 0.6; ts 1, 3, none.
"""

from __future__ import annotations

import contextlib
import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

GRAPH = "r200:g"
TERM = "r200token"
IDS = {k: f"r200:{k}" for k in "abc"}
DOCS = {"a": f"{TERM} once", "c": f"{TERM} {TERM} {TERM}"}


def _width(conn) -> int:
    from iris_vector_graph.schema import GraphSchema

    cursor = conn.cursor()
    try:
        width = GraphSchema.get_embedding_dimension(cursor, "Graph_KG.kg_NodeEmbeddings")
    finally:
        cursor.close()
    assert width, "kg_NodeEmbeddings declares no vector width"
    return int(width)


def _vector(width, x, y):
    return "[" + ",".join(str(v) for v in [x, y] + [0.0] * (width - 2)) + "]"


def _wipe(conn):
    cursor = conn.cursor()
    try:
        for table in ("kg_NodeEmbeddings", "docs"):
            cursor.execute(f"DELETE FROM Graph_KG.{table} WHERE graph_id = ?", [GRAPH])
        conn.commit()
    finally:
        cursor.close()


@pytest.fixture(scope="module")
def setup(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    width = _width(iris_connection)
    engine = IRISGraphEngine(iris_connection, embedding_dimension=width)
    result = engine.initialize_schema()
    _wipe(iris_connection)
    with contextlib.suppress(Exception):
        engine.erase_graph(GRAPH)
    vectors = {"a": (1.0, 0.0), "b": (0.6, 0.8), "c": (0.0, 1.0)}
    for k, nid in IDS.items():
        engine.create_node(nid, labels=["R200"], graph=GRAPH)
    cursor = iris_connection.cursor()
    try:
        for k, nid in IDS.items():
            cursor.execute(
                "INSERT INTO Graph_KG.kg_NodeEmbeddings (graph_id, node_id, emb) VALUES (?, ?, TO_VECTOR(?, DOUBLE, ?))",
                (GRAPH, nid, _vector(width, *vectors[k]), width),
            )
        for k, text in DOCS.items():
            cursor.execute("INSERT INTO Graph_KG.docs (graph_id, id, text) VALUES (?, ?, ?)", (GRAPH, IDS[k], text))
        iris_connection.commit()
    finally:
        cursor.close()
    try:
        yield engine, result, width
    finally:
        _wipe(iris_connection)
        with contextlib.suppress(Exception):
            engine.erase_graph(GRAPH)


def test_setup_creates_the_procedure(setup, iris_connection):
    engine, result, _ = setup
    assert "kg_RRF_FUSE" not in result["routines"]["failed"], result["routines"]
    cursor = iris_connection.cursor()
    try:
        cursor.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.ROUTINES WHERE ROUTINE_SCHEMA = 'Graph_KG' "
            "AND ROUTINE_NAME = 'kg_RRF_FUSE'"
        )
        assert cursor.fetchone()[0] == 1
    finally:
        cursor.close()
    assert engine.status().missing_routines == []


def test_the_procedure_fuses_both_legs(setup, iris_connection):
    _, _, width = setup
    cursor = iris_connection.cursor()
    try:
        cursor.execute(
            "SELECT id, rrf, vs, ts FROM Graph_KG.kg_RRF_FUSE(?, ?, ?, ?, ?, ?, ?)",
            (10, 10, 10, 60, _vector(width, 1.0, 0.0), TERM, GRAPH),
        )
        rows = cursor.fetchall()
    finally:
        cursor.close()
    names = {v: k for k, v in IDS.items()}
    got = [
        (names.get(r[0], r[0]), round(float(r[1]), 9), round(float(r[2]), 6),
         None if r[3] is None else round(float(r[3]), 6))
        for r in rows
    ]
    assert got == [
        ("a", round(1 / 61 + 1 / 62, 9), 1.0, 1.0),
        ("c", round(1 / 63 + 1 / 61, 9), 0.0, 3.0),
        ("b", round(1 / 62 + 1 / (60 + 1_000_000_000), 9), 0.6, None),
    ]
