"""`SHOW INDEXES` reports indexes that exist (spec 226, US5 / FR-018–019).

`_show_indexes` used to append a `hnsw_node_embeddings` row unconditionally, deriving its
state from a row count: `ONLINE` when `kg_NodeEmbeddings_optimized` had rows, `BUILDING`
when it did not. Neither is an index. `BUILDING` is the worse of the two — it tells an
operator to wait for something that is not happening.

Measured on `ivg-iris-enterprise` while writing these tests: an HNSW index **cannot** exist
on `kg_NodeEmbeddings` or `kg_NodeEmbeddings_optimized` at all. Both have a VARCHAR `id` as
their IDKEY, and IRIS refuses ANN indices there (`ERROR #7222: %SQL.Index ANN indices are
only supported when the IDKEY is based on a single positive integer attribute`). So the
fabricated row promised a build that no amount of waiting could finish.
`TestHnswCannotExistOnTheEmbeddingTables` pins that refusal, because it is the reason the
documentation corrections in this phase are corrections and not a change of policy.
"""

import uuid

import pytest

from iris_vector_graph.schema import GraphSchema

_HNSW_ROW_NAME = "hnsw_node_embeddings"


def _index_rows(engine):
    result = engine._show_indexes()
    return {row[0]: row for row in result.rows}


def _hnsw_rows(engine):
    """Every reported row that claims to be an HNSW index, by whatever name."""
    return [row for row in engine._show_indexes().rows if "HNSW" in str(row[1]).upper()]


class TestNoIndexIsInvented:
    def test_no_hnsw_row_on_a_default_installation(self, engine):
        assert _hnsw_rows(engine) == []
        assert _HNSW_ROW_NAME not in _index_rows(engine)

    def test_rows_in_the_optimized_table_do_not_conjure_an_index(self, engine):
        """The row count was the old code's only input, so this is the decisive case:
        data in a table is not an index over it."""
        cursor = engine.conn.cursor()
        table = engine._t("kg_NodeEmbeddings_optimized")
        width = GraphSchema.get_embedding_dimension(cursor, table) or 4
        vector = ",".join(["0.1"] * width)
        ids = [f"IVG226_IDX:{uuid.uuid4().hex[:8]}:{i}" for i in range(3)]
        try:
            for node_id in ids:
                cursor.execute(
                    f"INSERT INTO {table} (id, emb) VALUES (?, TO_VECTOR(?, DOUBLE))",
                    [node_id, vector],
                )
            engine.conn.commit()

            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            assert int(cursor.fetchone()[0]) >= 3, "the premise of this test did not hold"

            assert _hnsw_rows(engine) == []
        finally:
            for node_id in ids:
                try:
                    cursor.execute(f"DELETE FROM {table} WHERE id = ?", [node_id])
                except Exception:
                    pass
            try:
                engine.conn.commit()
            except Exception:
                pass
            cursor.close()

    def test_the_rest_of_the_report_is_unchanged(self, engine):
        """Removing the invented row must not take the real rows with it."""
        rows = _index_rows(engine)
        assert "nkg_adjacency" in rows
        assert "pk_nodes" in rows


class TestARealHnswIndexIsReported:
    """The positive half. It cannot be asserted through `_show_indexes` on this schema —
    see the module docstring — so it is asserted at the seam `_show_indexes` reads."""

    @pytest.fixture
    def ann_table(self, engine):
        """A scratch table whose IDKEY is an integer, so an HNSW index is permitted."""
        name = f"ivg226_ann_{uuid.uuid4().hex[:8]}"
        table = f"Graph_KG.{name}"
        cursor = engine.conn.cursor()
        cursor.execute(f"CREATE TABLE {table} (id INT PRIMARY KEY, emb VECTOR(DOUBLE, 4))")
        engine.conn.commit()
        cursor.execute(f"CREATE INDEX {name}_hnsw ON {table} (emb) AS HNSW(Distance='Cosine')")
        engine.conn.commit()
        try:
            yield table, f"{name}_hnsw"
        finally:
            try:
                cursor.execute(f"DROP TABLE {table}")
                engine.conn.commit()
            except Exception:
                pass
            cursor.close()

    def test_a_real_index_is_found_under_its_real_name(self, engine, ann_table):
        table, index_name = ann_table
        found = engine._hnsw_indexes(table)
        assert [name for name, _ in found] == [index_name]
        assert found[0][1] == "emb"

    def test_a_table_without_one_reports_nothing(self, engine):
        assert engine._hnsw_indexes(engine._t("kg_NodeEmbeddings")) == []

    def test_a_table_that_does_not_exist_reports_nothing(self, engine):
        """`kg_NodeEmbeddings_optimized` is absent in DDL-only namespaces, and the report
        has to survive that rather than raise from the middle of `SHOW INDEXES`."""
        assert engine._hnsw_indexes("Graph_KG.ivg226_no_such_table") == []


class TestHnswCannotExistOnTheEmbeddingTables:
    """Why the removed row could never have become true."""

    def test_creating_one_is_refused_because_the_idkey_is_a_string(self, engine):
        cursor = engine.conn.cursor()
        table = engine._t("kg_NodeEmbeddings_optimized")
        with pytest.raises(Exception) as caught:
            cursor.execute(
                f"CREATE INDEX ivg226_refused ON {table} (emb) AS HNSW(Distance='Cosine')"
            )
            engine.conn.commit()
        message = str(caught.value)
        assert "7222" in message or "ANN" in message.upper()

        # And the refusal leaves nothing behind — no half-built index to report.
        assert engine._hnsw_indexes(table) == []
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        assert cursor.fetchone() is not None
        cursor.close()
