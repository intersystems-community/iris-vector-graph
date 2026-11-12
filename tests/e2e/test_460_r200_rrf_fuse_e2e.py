"""4.6.0 R200, live: after setup `Graph_KG.kg_RRF_FUSE` exists, on Community 2026.1 as on 2026.3; and (R265) a named
graph's vectors, which live in the graph's route, are fused by `/hybridSearch`'s code and refused by the procedure.

On the compose image (iris-community:2026.1) its CREATE failed at Prepare, `SQLCODE -1 ... SELECT expected, <end of
statement> found ^ WITH V AS (`: 2026.1 rejects a multi-line WITH in a procedure body. Setup logged it at debug level
and the procedure was simply absent. The body now joins derived tables instead of CTEs.

Until R265 this file wrote the graph r200:g's vectors straight into `kg_NodeEmbeddings`, the one table the procedure
reads, where no API call puts a named graph's vectors: `store_embedding(graph=...)` writes them to the graph's route
(spec 227). They are written that way now, so the procedure, whose FROM is fixed at `kg_NodeEmbeddings`, refuses the
graph, naming the calls that read the route; and `Graph.KG.PyOps.HybridSearch` (the `/hybridSearch` route) fuses the
route's vectors with the documents as the procedure fuses. The procedure's own fusion, on the default graph's
vectors (`kg_NodeEmbeddings` is that graph's route), is checked by tests/e2e/test_460_kg_webapp_least_privilege_e2e.py.

Worked by hand, in the graph r200:g of this namespace (vectors 3 wide, in r200:g's route):

    node  vector          doc                             cosine to q = [1,0,0]   occurrences of r200token
    a     [1, 0, 0]       "r200token once"                1.0  (rank 1)           1 (rank 2)
    b     [0.6, 0.8, 0]   none                            0.6  (rank 2)           -
    c     [0, 1, 0]       "r200token r200token r200token" 0.0  (rank 3)           3 (rank 1)

With c = 60 and a missing rank as 1000000000: rrf(a) = 1/61 + 1/62, rrf(c) = 1/63 + 1/61, rrf(b) = 1/62 + 1/(60 +
1e9). Order a, c, b; vs 1.0, 0.0, 0.6; ts 1, 3, none.
"""

from __future__ import annotations

import contextlib
import json
import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

GRAPH = "r200:g"
TERM = "r200token"
IDS = {k: f"r200:{k}" for k in "abc"}
DOCS = {"a": f"{TERM} once", "c": f"{TERM} {TERM} {TERM}"}
VECTORS = {"a": [1.0, 0.0, 0.0], "b": [0.6, 0.8, 0.0], "c": [0.0, 1.0, 0.0]}
CONTAINER = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
NS = os.environ.get("IVG_NAMESPACE", "USER")


def _wipe(engine, conn):
    cursor = conn.cursor()
    try:
        # the rows this file wrote into kg_NodeEmbeddings before R265, in a namespace an older run left them in
        for table in ("kg_NodeEmbeddings", "docs"):
            cursor.execute(f"DELETE FROM Graph_KG.{table} WHERE graph_id = ?", [GRAPH])
        conn.commit()
    finally:
        cursor.close()
    with contextlib.suppress(Exception):
        engine.erase_graph(GRAPH)
    conn.commit()


@pytest.fixture(scope="module")
def setup(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    from iris_vector_graph.schema import GraphSchema

    cursor = iris_connection.cursor()
    try:
        width = GraphSchema.get_embedding_dimension(cursor, "Graph_KG.kg_NodeEmbeddings")
    finally:
        cursor.close()
    assert width, "kg_NodeEmbeddings declares no vector width"
    result = IRISGraphEngine(iris_connection, embedding_dimension=int(width)).initialize_schema()
    # the writes go through an engine with no width of its own: r200:g's route takes its vectors' (3)
    engine = IRISGraphEngine(iris_connection)
    _wipe(engine, iris_connection)
    for k, nid in IDS.items():
        engine.create_node(nid, labels=["R200"], graph=GRAPH)
        assert engine.store_embedding(nid, VECTORS[k], graph=GRAPH)
    cursor = iris_connection.cursor()
    try:
        for k, text in DOCS.items():
            cursor.execute("INSERT INTO Graph_KG.docs (graph_id, id, text) VALUES (?, ?, ?)", (GRAPH, IDS[k], text))
        iris_connection.commit()
    finally:
        cursor.close()
    route = engine.resolve_route(graph=GRAPH, model_key=None)
    assert route is not None and route.table_name.startswith("kg_emb_"), route
    try:
        yield engine, result, route.table_name
    finally:
        _wipe(engine, iris_connection)


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


def test_the_procedure_refuses_the_graphs_route(setup, iris_connection):
    """An error naming the route and the calls that read it, never the zero rows it answered before R265."""
    _, _, route = setup
    cursor = iris_connection.cursor()
    try:
        with pytest.raises(Exception) as caught:
            cursor.execute(
                "SELECT id, rrf, vs, ts FROM Graph_KG.kg_RRF_FUSE(?, ?, ?, ?, ?, ?, ?)",
                (10, 10, 10, 60, json.dumps(VECTORS["a"]), TERM, GRAPH),
            )
            cursor.fetchall()
    finally:
        cursor.close()
    for named in (route, GRAPH, "engine.kg_RRF_FUSE", "ivg.retrieve"):
        assert named in str(caught.value), str(caught.value)


def test_hybrid_search_fuses_both_legs_from_the_route(setup):
    script = (
        f'write "<<H:",##class(Graph.KG.PyOps).HybridSearch([1,0,0],"{TERM}",10,60,"{GRAPH}").%ToJSON(),":H>>",!\n'
        "halt\n"
    )
    from tests.helpers.iris_lines import session

    out = session(script, NS, timeout=180)  # over TCP (4.7.0: no `iris session`)
    assert "<<H:" in out, out
    rows = json.loads(out.split("<<H:", 1)[1].split(":H>>", 1)[0])
    names = {v: k for k, v in IDS.items()}
    got = [
        (names.get(r["id"], r["id"]), round(r["score"], 9),
         None if r["extras"]["vs"] is None else round(r["extras"]["vs"], 6), r["extras"]["bm25"])
        for r in rows
    ]
    assert got == [
        ("a", round(1 / 61 + 1 / 62, 9), 1.0, 1.0),
        ("c", round(1 / 63 + 1 / 61, 9), 0.0, 3.0),
        ("b", round(1 / 62 + 1 / (60 + 1_000_000_000), 9), 0.6, None),
    ], out
