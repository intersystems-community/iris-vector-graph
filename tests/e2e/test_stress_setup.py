import os
import time
import uuid
import contextlib

import pytest
from tests.e2e import namespace_width

IRIS_HOST = os.environ.get("IRIS_HOST", "localhost")
IRIS_PORT = int(os.environ.get("IVG_PORT", "31972"))
IRIS_NS = os.environ.get("IVG_NAMESPACE", "USER")  # the run's namespace (R266: IRIS_NAMESPACE put scratch runs in USER)
IRIS_USER = os.environ.get("IRIS_USERNAME", "_SYSTEM")
IRIS_PASS = os.environ.get("IRIS_PASSWORD", "SYS")


@pytest.fixture(scope="module")
def raw_conn(iris_connection):
    return iris_connection


@pytest.fixture
def fresh_engine(raw_conn):
    from iris_vector_graph.engine import IRISGraphEngine
    e = IRISGraphEngine(raw_conn, embedding_dimension=namespace_width.of(raw_conn))
    e.initialize_schema()
    yield e


class TestColdInit:

    def test_initialize_schema_creates_tables(self, raw_conn):
        from iris_vector_graph.engine import IRISGraphEngine
        e = IRISGraphEngine(raw_conn, embedding_dimension=namespace_width.of(raw_conn))
        e.initialize_schema()
        cur = raw_conn.cursor()
        cur.execute("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'Graph_KG'")
        count = cur.fetchone()[0]
        assert count >= 5, f"Expected ≥5 Graph_KG tables, got {count}"

    def test_initialize_schema_is_idempotent(self, raw_conn):
        from iris_vector_graph.engine import IRISGraphEngine
        e = IRISGraphEngine(raw_conn, embedding_dimension=namespace_width.of(raw_conn))
        for _ in range(3):
            e.initialize_schema()
        # Should not raise, should not duplicate tables
        cur = raw_conn.cursor()
        cur.execute("SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = 'Graph_KG'")
        count = cur.fetchone()[0]
        assert count >= 5

    def test_schema_validation_passes_after_init(self, fresh_engine):
        from iris_vector_graph.schema import GraphSchema
        cur = fresh_engine.conn.cursor()
        result = GraphSchema.validate_schema(cur)
        assert isinstance(result, dict), f"validate_schema returned {type(result)}"
        all_ok = result.get("valid", all(v for v in result.values() if isinstance(v, bool)))
        assert all_ok, f"Schema validation failed: {result}"

    def test_objectscript_classes_deployed(self, raw_conn):
        from iris_vector_graph.schema import GraphSchema
        cur = raw_conn.cursor()
        status = GraphSchema.check_objectscript_classes(cur)
        if hasattr(status, "items"):
            missing = [k for k, v in status.items() if not v]
            assert not missing, f"Missing ObjectScript classes: {missing}"
        else:
            assert status is not None

    def test_engine_status_after_init(self, fresh_engine):
        s = fresh_engine.status()
        assert s is not None
        assert hasattr(s, "schema_ready") or hasattr(s, "report")


class TestConnectionModes:

    def test_from_connect_classmethod(self):
        from iris_vector_graph.engine import IRISGraphEngine
        try:
            e = IRISGraphEngine.from_connect(
                hostname=IRIS_HOST, port=IRIS_PORT,
                namespace=IRIS_NS, username=IRIS_USER, password=IRIS_PASS,
                embedding_dimension=4,
            )
            assert e is not None
            e.conn.close()
        except Exception as exc:
            pytest.skip(f"from_connect not available or IRIS unavailable: {exc}")

    def test_is_ready_returns_bool(self, fresh_engine):
        # `is_ready` is a method and has been for as long as it has existed, so the
        # callable-or-property hedge and the `AttributeError` skip both described a
        # shape the engine never had — and the skip would have hidden its removal.
        assert isinstance(fresh_engine.is_ready(), bool)

    def test_multiple_connections_same_db(self, raw_conn):
        import iris
        from iris_vector_graph.engine import IRISGraphEngine
        conns = []
        try:
            for _ in range(3):
                c = iris.connect(IRIS_HOST, IRIS_PORT, IRIS_NS, IRIS_USER, IRIS_PASS)
                conns.append(IRISGraphEngine(c, embedding_dimension=4))
            assert len(conns) == 3
        finally:
            for eng in conns:
                with contextlib.suppress(Exception):
                    eng.conn.close()

    def test_engine_survives_connection_reuse(self, fresh_engine):
        pfx = f"conn_{uuid.uuid4().hex[:6]}"
        fresh_engine.create_node(f"{pfx}:n1", labels=["ConnTest"])
        fresh_engine.create_node(f"{pfx}:n2", labels=["ConnTest"])
        fresh_engine.create_edge(f"{pfx}:n1", "CONN_EDGE", f"{pfx}:n2")
        r = fresh_engine.execute_cypher(
            f"MATCH (a:ConnTest)-[:CONN_EDGE]->(b:ConnTest) WHERE a.node_id STARTS WITH '{pfx}' RETURN a.node_id, b.node_id"
        )
        assert len(r.rows) >= 1


class TestSchemaMigration:

    def test_add_graph_id_column_idempotent(self, raw_conn):
        from iris_vector_graph.schema import GraphSchema
        cur = raw_conn.cursor()
        for _ in range(2):
            with contextlib.suppress(Exception):
                GraphSchema.add_graph_id_column(cur)
                raw_conn.commit()

    def test_add_graph_id_index_idempotent(self, raw_conn):
        from iris_vector_graph.schema import GraphSchema
        cur = raw_conn.cursor()
        for _ in range(2):
            with contextlib.suppress(Exception):
                GraphSchema.add_graph_id_index(cur)
                raw_conn.commit()

    def test_update_spo_unique_constraint_idempotent(self, raw_conn):
        from iris_vector_graph.schema import GraphSchema
        cur = raw_conn.cursor()
        with contextlib.suppress(Exception):
            GraphSchema.update_spo_unique_constraint(cur)
            raw_conn.commit()

    def test_rebuild_indexes_after_bulk_insert(self, fresh_engine):
        from iris_vector_graph.schema import GraphSchema
        pfx = f"idx_{uuid.uuid4().hex[:6]}"
        nodes = [{"id": f"{pfx}:n{i}", "labels": ["IdxTest"]} for i in range(100)]
        GraphSchema.disable_indexes(fresh_engine.conn.cursor())
        fresh_engine.bulk_create_nodes(nodes)
        fresh_engine.conn.commit()
        GraphSchema.rebuild_indexes(fresh_engine.conn.cursor())
        fresh_engine.conn.commit()
        count = fresh_engine.get_node_count(label="IdxTest")
        assert count >= 100


VECTOR_TABLES = ("kg_NodeEmbeddings", "kg_NodeEmbeddings_optimized", "kg_EdgeEmbeddings")


def _widths(conn) -> dict:
    from iris_vector_graph.schema import GraphSchema

    return {t: GraphSchema.get_embedding_dimension(conn.cursor(), f"Graph_KG.{t}") for t in VECTOR_TABLES}


class TestEmbeddingDimension:
    """4.7.0 row 25: setup re-declares the namespace's vector columns only when asked (`rewidth=True`) and only while
    they are empty, which `iris_master_cleanup` makes them; the per-test guard in tests/conftest.py puts the session
    width back after. Both cases used to skip or swallow whatever happened."""

    def test_different_dimensions_initialize_correctly(self, raw_conn, iris_master_cleanup):
        from iris_vector_graph.engine import IRISGraphEngine
        for dim in [4, 64]:
            IRISGraphEngine(raw_conn, embedding_dimension=dim).initialize_schema(rewidth=True)
            assert _widths(raw_conn) == {t: dim for t in VECTOR_TABLES}

    def test_dimension_mismatch_logged(self, raw_conn, iris_master_cleanup):
        from iris_vector_graph import EmbeddingIdentityConflict
        from iris_vector_graph.engine import IRISGraphEngine
        IRISGraphEngine(raw_conn, embedding_dimension=768).initialize_schema(rewidth=True)
        with pytest.raises(EmbeddingIdentityConflict) as info:
            IRISGraphEngine(raw_conn, embedding_dimension=4).initialize_schema()
        assert "VECTOR(DOUBLE, 768)" in str(info.value) and "embedding_dimension is 4" in str(info.value)
        assert _widths(raw_conn) == {t: 768 for t in VECTOR_TABLES}, "the refused setup changed a column"
