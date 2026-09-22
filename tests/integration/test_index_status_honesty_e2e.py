"""`SHOW INDEXES` reports indexes that exist (spec 226, US5 / FR-018–019).

`_show_indexes` used to append a `hnsw_node_embeddings` row unconditionally, deriving its
state from a row count: `ONLINE` when `kg_NodeEmbeddings_optimized` had rows, `BUILDING`
when it did not. Neither is an index. `BUILDING` is the worse of the two — it tells an
operator to wait for something that is not happening.

In 3.2.0 that row promised a build no amount of waiting could finish: both embedding
tables had a VARCHAR `id` as their IDKEY, and IRIS refuses ANN indices there (`ERROR
#7222: %SQL.Index ANN indices are only supported when the IDKEY is based on a single
positive integer attribute`).

4.0.0 removes that obstacle. The embedding tables are keyed `emb_rowid BIGINT IDENTITY
PRIMARY KEY` with `UNIQUE (graph_id, node_id)` beside it, so an HNSW index over `emb` is
now permitted and `TestARealIndexOnTheEmbeddingTableIsReported` builds one. That makes the
honesty of the report matter more, not less: an index that can exist has to be reported
when it does and only when it does, from `%Dictionary.CompiledIndex` rather than from a
row count. Any index these tests build is dropped again — one left behind is reported to
every later test as a real index, and blocks `ALTER COLUMN emb` besides (`ERROR #5414:
Vector without a fixed length is not allowed in %SQL.Index functional indices`).
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
        nodes_table = engine._t("nodes")
        try:
            for node_id in ids:
                # 4.0.0's `fk_emb_node_opt` is composite — (graph_id, node_id) against
                # `nodes` — so an embedding for an unregistered node is refused with
                # SQLCODE -121 and this test failed before reaching its assertion.
                cursor.execute(
                    f"INSERT INTO {nodes_table} (node_id, graph_id) VALUES (?, '')",
                    [node_id],
                )
            engine.conn.commit()
            for node_id in ids:
                # 4.0.0 columns: `graph_id` + `node_id`, no `id`. Spelling `graph_id` out
                # rather than leaning on its DEFAULT '', because the default graph being
                # '' is the thing under test everywhere else in this suite.
                cursor.execute(
                    f"INSERT INTO {table} (graph_id, node_id, emb) "
                    f"VALUES ('', ?, TO_VECTOR(?, DOUBLE))",
                    [node_id, vector],
                )
            engine.conn.commit()

            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            assert int(cursor.fetchone()[0]) >= 3, "the premise of this test did not hold"

            assert _hnsw_rows(engine) == []
        finally:
            for node_id in ids:
                try:
                    cursor.execute(f"DELETE FROM {table} WHERE node_id = ?", [node_id])
                except Exception:
                    pass
            for node_id in ids:
                try:
                    cursor.execute(
                        f"DELETE FROM {nodes_table} WHERE node_id = ?", [node_id]
                    )
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
    """The seam `_show_indexes` reads, exercised on a table of this test's own making.

    Kept separate from the embedding table below: a scratch table proves `_hnsw_indexes`
    reads the dictionary without putting an index on schema every other test shares.
    """

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


class TestARealIndexOnTheEmbeddingTableIsReported:
    """4.0.0's integer `emb_rowid` IDKEY makes an HNSW index on the embedding table legal.

    3.2.0's version of this class asserted the opposite, and asserted it by *attempting*
    the create — so once the create started succeeding, the test both failed and left a
    live ONLINE index on `kg_NodeEmbeddings_optimized`, which every later reader then
    truthfully reported. Built and dropped inside one test here for that reason.
    """

    @pytest.fixture
    def index_on_optimized(self, engine):
        table = engine._t("kg_NodeEmbeddings_optimized")
        name = f"ivg227_ann_{uuid.uuid4().hex[:8]}"
        cursor = engine.conn.cursor()
        cursor.execute(f"CREATE INDEX {name} ON {table} (emb) AS HNSW(Distance='Cosine')")
        engine.conn.commit()
        try:
            yield table, name
        finally:
            try:
                cursor.execute(f"DROP INDEX {name} ON {table}")
                engine.conn.commit()
            except Exception:
                pass
            cursor.close()

    def test_the_index_is_reported_under_its_real_name(self, engine, index_on_optimized):
        table, name = index_on_optimized
        assert [n for n, _ in engine._hnsw_indexes(table)] == [name]

        reported = _hnsw_rows(engine)
        assert [row[0] for row in reported] == [name]
        assert _HNSW_ROW_NAME not in _index_rows(engine), (
            "the report named the real index and invented the old one as well"
        )

    def test_dropping_it_stops_the_report(self, engine, index_on_optimized):
        """The other half of honesty: the row goes when the index does."""
        table, name = index_on_optimized
        cursor = engine.conn.cursor()
        try:
            cursor.execute(f"DROP INDEX {name} ON {table}")
            engine.conn.commit()
        finally:
            cursor.close()

        assert engine._hnsw_indexes(table) == []
        assert _hnsw_rows(engine) == []
