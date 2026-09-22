"""A snapshot of a routed installation has to bring the routes back.

Requires ivg-iris-enterprise:
    IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972 \
        pytest tests/integration/test_snapshot_routed_embeddings.py

Spec 227 moved each `(graph, model)` pair's vectors into its own physical table,
named `kg_emb_<hash>` and claimed by a row in `Graph_KG.embedding_registry`. Those
tables are deliberately *not* inventory entries one by one — which of them exists is
a fact about the registry, not about the schema — and that is exactly how the
snapshot lost them: `STORE_PLAN` named neither the registry nor any route, so
`save_snapshot` wrote an archive with no record that the routes existed, and
`restore_snapshot` reported success over a database whose every scoped search was a
miss.

The two halves both matter and fail differently:

* without the registry rows, the routed tables could be recreated and still be
  orphans — `resolve_route` reads the registry and nothing else (FR-011), so a
  table full of live vectors that no row names is unreachable;
* without the routed tables, the registry rows name tables that are not there.

The fixture is the one the spec calls decisive: two graphs holding the same node ID
under different models at different widths. It is the shape that cannot be faked in
Python, because the guarantee is in the column declarations — a routed column
enforces its width at INSERT (SQLCODE -104), so a restore that guesses a width
produces a table whose first write fails.
"""

from __future__ import annotations

import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.security import is_routed_embedding_table

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true",
    reason="SKIP_IRIS_TESTS is set",
)

GRAPH_A = "snaproute_a"
GRAPH_B = "snaproute_b"
MODEL_A = "snaproute-model-a"
MODEL_B = "snaproute-model-b"
WIDTH_A = 8
WIDTH_B = 16
#: The same node ID in both graphs — the collision `uq_nodes_nodeid` used to forbid.
NODE = "snaproute:shared"


@pytest.fixture()
def routed(iris_connection, iris_master_cleanup):
    """Two graphs, one node ID, two models, two widths, two routed tables."""
    engine = IRISGraphEngine(iris_connection, embedding_dimension=WIDTH_A)
    engine.initialize_schema(auto_deploy_objectscript=False)

    engine.create_node(NODE, graph=GRAPH_A)
    engine.create_node(NODE, graph=GRAPH_B)
    engine.create_node("snaproute:a2", graph=GRAPH_A)

    engine.resolve_route(GRAPH_A, MODEL_A, create=True, dimension=WIDTH_A)
    engine.resolve_route(GRAPH_B, MODEL_B, create=True, dimension=WIDTH_B)

    engine.store_embedding(
        NODE, [0.1] * WIDTH_A, graph=GRAPH_A, model_key=MODEL_A, metadata={"src": "a"}
    )
    engine.store_embedding(
        "snaproute:a2", [0.2] * WIDTH_A, graph=GRAPH_A, model_key=MODEL_A
    )
    engine.store_embedding(
        NODE, [0.3] * WIDTH_B, graph=GRAPH_B, model_key=MODEL_B, metadata={"src": "b"}
    )
    return engine


def _route_tables(engine) -> dict:
    return {
        (GRAPH_A, MODEL_A): engine.resolve_route(GRAPH_A, MODEL_A),
        (GRAPH_B, MODEL_B): engine.resolve_route(GRAPH_B, MODEL_B),
    }


def _declared_width(engine, table_name: str):
    from iris_vector_graph.schema import GraphSchema

    cursor = engine.conn.cursor()
    try:
        return GraphSchema.get_embedding_dimension(cursor, f"Graph_KG.{table_name}")
    finally:
        cursor.close()


# --- the fixture itself is the premise, so it is checked ---------------------


def test_the_two_graphs_start_out_on_separate_routed_tables(routed):
    routes = _route_tables(routed)
    a = routes[(GRAPH_A, MODEL_A)]
    b = routes[(GRAPH_B, MODEL_B)]

    assert a is not None and b is not None, "the fixture did not create both routes"
    assert a.table_name != b.table_name, "both models landed on one table"
    assert is_routed_embedding_table(a.table_name)
    assert is_routed_embedding_table(b.table_name)
    assert (a.dimension, b.dimension) == (WIDTH_A, WIDTH_B)


# --- the round trip ---------------------------------------------------------


def test_a_routed_installation_survives_a_snapshot_round_trip(routed, tmp_path):
    """The whole point, in one test: save, erase, restore, read the vectors back."""
    before = _route_tables(routed)
    path = str(tmp_path / "routed.zip")

    routed.save_snapshot(path, layers=["sql"])
    routed.erase_all()

    # Erased means erased: the routes are gone before the restore puts them back, so
    # a pass here cannot be the pre-existing state surviving the test.
    assert routed.resolve_route(GRAPH_A, MODEL_A) is None
    assert routed.resolve_route(GRAPH_B, MODEL_B) is None

    result = routed.restore_snapshot(path)
    assert isinstance(result, dict)

    after = _route_tables(routed)
    for key, route in before.items():
        restored = after[key]
        assert restored is not None, (
            f"{key} has no route after the restore, so every search in that graph "
            f"reports a miss over vectors the archive holds: {result}"
        )
        assert restored.table_name == route.table_name, (
            "the route came back under a different table name, so the registry row "
            "and the table the archive carried no longer agree"
        )
        assert restored.dimension == route.dimension

    a = routed.get_embedding(NODE, graph=GRAPH_A, model_key=MODEL_A)
    b = routed.get_embedding(NODE, graph=GRAPH_B, model_key=MODEL_B)
    assert a is not None, "graph A's vector did not come back"
    assert b is not None, "graph B's vector did not come back"
    assert len(a["embedding"]) == WIDTH_A
    assert len(b["embedding"]) == WIDTH_B


def test_the_restored_columns_are_declared_at_their_own_widths(routed, tmp_path):
    """A restore that guesses one width produces a table whose next write fails.

    IRIS enforces the declared VECTOR width at INSERT, so the widths have to come out
    of the archive rather than out of the restoring engine's `embedding_dimension`
    (which is WIDTH_A here, and wrong for graph B).
    """
    before = _route_tables(routed)
    path = str(tmp_path / "widths.zip")

    routed.save_snapshot(path, layers=["sql"])
    routed.erase_all()
    routed.restore_snapshot(path)

    assert _declared_width(routed, before[(GRAPH_A, MODEL_A)].table_name) == WIDTH_A
    assert _declared_width(routed, before[(GRAPH_B, MODEL_B)].table_name) == WIDTH_B

    # And a write at the restored width lands, which is the operational form of the
    # same claim.
    routed.create_node("snaproute:b2", graph=GRAPH_B)
    assert routed.store_embedding(
        "snaproute:b2", [0.4] * WIDTH_B, graph=GRAPH_B, model_key=MODEL_B
    )


def test_each_restored_graph_holds_only_its_own_rows(routed, tmp_path):
    """The restore must not pour both graphs' vectors into one table."""
    before = _route_tables(routed)
    path = str(tmp_path / "scope.zip")

    routed.save_snapshot(path, layers=["sql"])
    routed.erase_all()
    routed.restore_snapshot(path)

    cursor = routed.conn.cursor()
    try:
        for (graph, _model), route in before.items():
            cursor.execute(
                f"SELECT COUNT(*) FROM Graph_KG.{route.table_name} "
                "WHERE COALESCE(graph_id, '') <> ?",
                [graph],
            )
            assert cursor.fetchone()[0] == 0, (
                f"{route.table_name} holds rows from a graph other than {graph}"
            )
        cursor.execute(
            f"SELECT COUNT(*) FROM Graph_KG.{before[(GRAPH_A, MODEL_A)].table_name}"
        )
        assert cursor.fetchone()[0] == 2, "graph A's two vectors did not both return"
    finally:
        cursor.close()


def test_the_restored_index_state_is_what_the_database_actually_has(routed, tmp_path):
    """The archived `index_state` is a claim about the database the archive came from.

    A restored route's table was created seconds ago. Copying the archived state
    forward would reproduce exactly what spec 226 removed: a report of an HNSW index
    that is not in `%Dictionary.CompiledIndex`. So the restore re-attempts the build
    and records whatever this database answers.
    """
    path = str(tmp_path / "index.zip")
    routed.save_snapshot(path, layers=["sql"])
    routed.erase_all()
    routed.restore_snapshot(path)

    from iris_vector_graph.schema import GraphSchema

    cursor = routed.conn.cursor()
    try:
        for graph, model in ((GRAPH_A, MODEL_A), (GRAPH_B, MODEL_B)):
            route = routed.resolve_route(graph, model)
            assert route is not None
            assert route.index_state in ("present", "refused"), (
                f"{route.table_name} came back with index_state={route.index_state!r}; "
                "the restore has to record an attempt, not leave the archive's claim"
            )
            compiled = GraphSchema.hnsw_indexes(cursor, f"Graph_KG.{route.table_name}")
            if route.index_state == "present":
                assert compiled, (
                    f"{route.table_name} claims an ANN index that is not in "
                    "%Dictionary.CompiledIndex — the archived state was copied forward"
                )
            else:
                assert not compiled, (
                    f"{route.table_name} reports a refused index while one is compiled"
                )
    finally:
        cursor.close()
