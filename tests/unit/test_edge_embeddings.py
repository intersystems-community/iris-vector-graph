"""Unit and E2E tests for edge embedding pipeline (spec 065).

Unit tests use mocked IRIS connection — no container required.
E2E tests run against the live iris-vector-graph container.
"""
import os
import uuid
from unittest.mock import MagicMock, patch, call

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


def _make_engine(embedding_dimension=4):
    from iris_vector_graph.engine import IRISGraphEngine

    conn = MagicMock()
    conn.cursor.return_value = MagicMock()
    engine = IRISGraphEngine(conn, embedding_dimension=embedding_dimension)
    engine.embedder = MagicMock(side_effect=lambda text: [0.1] * embedding_dimension)
    return engine


# ================================================================
# Unit tests — no IRIS container required
# ================================================================


class TestEdgeEmbeddingsUnit:

    def test_schema_ddl_contains_kg_edge_embeddings(self):
        from iris_vector_graph.schema import GraphSchema

        sql = GraphSchema.get_base_schema_sql(embedding_dimension=4)
        assert "kg_EdgeEmbeddings" in sql

    def test_embed_edges_default_text_fn(self):
        engine = _make_engine()
        cursor = engine.conn.cursor.return_value

        cursor.fetchall.side_effect = [
            [("s1", "p1", "o1"), ("s2", "p2", "o2")],
            [],
        ]

        texts_embedded = []
        original_embed = engine.embed_text

        def capturing_embed(text):
            texts_embedded.append(text)
            return [0.1] * 4

        engine.embed_text = capturing_embed

        engine.embed_edges(force=True)

        assert "s1 p1 o1" in texts_embedded
        assert "s2 p2 o2" in texts_embedded

    def test_embed_edges_custom_text_fn(self):
        engine = _make_engine()
        cursor = engine.conn.cursor.return_value

        cursor.fetchall.side_effect = [
            [("NodeA", "REL", "NodeB")],
            [],
        ]

        texts_embedded = []

        def capturing_embed(text):
            texts_embedded.append(text)
            return [0.1] * 4

        engine.embed_text = capturing_embed

        custom_fn = lambda s, p, o: f"{s.lower()}|{p.lower()}|{o.lower()}"
        engine.embed_edges(text_fn=custom_fn, force=True)

        assert "nodea|rel|nodeb" in texts_embedded

    def test_embed_edges_text_fn_returns_none_counted_as_skipped(self):
        engine = _make_engine()
        cursor = engine.conn.cursor.return_value

        cursor.fetchall.side_effect = [
            [("s1", "p1", "o1"), ("s2", "p2", "o2")],
            [],
        ]

        engine.embed_text = MagicMock(return_value=[0.1] * 4)

        result = engine.embed_edges(text_fn=lambda s, p, o: None, force=True)

        assert result["skipped"] == 2
        assert result["embedded"] == 0
        assert result["errors"] == 0

    def test_embed_edges_text_fn_raises_counted_as_error(self):
        engine = _make_engine()
        cursor = engine.conn.cursor.return_value

        cursor.fetchall.side_effect = [
            [("s1", "p1", "o1"), ("s2", "p2", "o2")],
            [],
        ]

        call_count = [0]

        def raising_fn(s, p, o):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ValueError("boom")
            return f"{s} {p} {o}"

        engine.embed_text = MagicMock(return_value=[0.1] * 4)
        result = engine.embed_edges(text_fn=raising_fn, force=True)

        assert result["errors"] == 1
        assert result["embedded"] == 1

    def test_embed_edges_unsafe_where_raises(self):
        engine = _make_engine()

        with pytest.raises(ValueError, match="[Uu]nsafe"):
            engine.embed_edges(where="p = 'x'; DROP TABLE nodes")

    def test_default_text_fn_format(self):
        engine = _make_engine()
        cursor = engine.conn.cursor.return_value

        cursor.fetchall.side_effect = [
            [("A", "REL", "B")],
            [],
        ]

        texts = []
        engine.embed_text = lambda t: (texts.append(t), [0.1] * 4)[1]

        engine.embed_edges(force=True)

        assert texts == ["A REL B"]

    def test_edge_vector_search_sql_shape(self):
        engine = _make_engine()
        cursor = engine.conn.cursor.return_value
        cursor.fetchall.return_value = []

        engine.edge_vector_search([0.1, 0.2, 0.3, 0.4], top_k=5)

        executed_sql = cursor.execute.call_args[0][0]
        assert "VECTOR_COSINE" in executed_sql
        assert "TO_VECTOR" in executed_sql
        assert "kg_EdgeEmbeddings" in executed_sql
        assert "score DESC" in executed_sql.upper() or "ORDER BY" in executed_sql.upper()


# ================================================================
# E2E tests — live IRIS container required
# ================================================================


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestEdgeEmbeddingsE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine

        self.conn = iris_connection
        self.engine = IRISGraphEngine(iris_connection, embedding_dimension=4)
        self._run = uuid.uuid4().hex[:8]
        self.engine.initialize_schema()

        self.engine.embed_text = lambda text: [
            hash(text) % 100 / 100.0,
            (hash(text) >> 8) % 100 / 100.0,
            (hash(text) >> 16) % 100 / 100.0,
            (hash(text) >> 24) % 100 / 100.0,
        ]
        yield

        cursor = self.conn.cursor()
        try:
            cursor.execute(
                f"DELETE FROM Graph_KG.kg_EdgeEmbeddings WHERE s LIKE 'ee65_{self._run}%'"
            )
            cursor.execute(
                f"DELETE FROM Graph_KG.rdf_edges WHERE s LIKE 'ee65_{self._run}%'"
            )
            cursor.execute(
                f"DELETE FROM Graph_KG.nodes WHERE node_id LIKE 'ee65_{self._run}%'"
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()

    def _make_nodes_and_edges(self, n: int):
        cursor = self.conn.cursor()
        edges = []
        for i in range(n):
            s = f"ee65_{self._run}_s{i}"
            o = f"ee65_{self._run}_o{i}"
            p = f"REL_{i % 3}"
            for nid in [s, o]:
                cursor.execute(
                    "INSERT INTO Graph_KG.nodes (node_id) SELECT ? WHERE NOT EXISTS "
                    "(SELECT 1 FROM Graph_KG.nodes WHERE node_id = ?)",
                    [nid, nid],
                )
            cursor.execute(
                "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) SELECT ?, ?, ? WHERE NOT EXISTS "
                "(SELECT 1 FROM Graph_KG.rdf_edges WHERE s=? AND p=? AND o_id=?)",
                [s, p, o, s, p, o],
            )
            edges.append((s, p, o))
        self.conn.commit()
        return edges

    def test_schema_creates_kg_edge_embeddings(self):
        cursor = self.conn.cursor()
        cursor.execute("SELECT TOP 1 s FROM Graph_KG.kg_EdgeEmbeddings")
        assert cursor.description is not None

        self.engine.initialize_schema()
        cursor.execute("SELECT TOP 1 s FROM Graph_KG.kg_EdgeEmbeddings")
        assert cursor.description is not None

    def test_embed_edges_all_default(self):
        edges = self._make_nodes_and_edges(5)

        result = self.engine.embed_edges(
            where=f"s LIKE 'ee65_{self._run}%'",
            force=True,
        )
        assert result["embedded"] == 5
        assert result["errors"] == 0

        result2 = self.engine.embed_edges(
            where=f"s LIKE 'ee65_{self._run}%'",
            force=False,
        )
        assert result2["embedded"] == 0
        assert result2["skipped"] == 5

    def test_embed_edges_force_true(self):
        edges = self._make_nodes_and_edges(3)

        self.engine.embed_edges(where=f"s LIKE 'ee65_{self._run}%'", force=True)

        result = self.engine.embed_edges(
            where=f"s LIKE 'ee65_{self._run}%'",
            force=True,
        )
        assert result["embedded"] == 3
        assert result["skipped"] == 0

    def test_embed_edges_where_filter(self):
        self._make_nodes_and_edges(6)

        result = self.engine.embed_edges(
            where=f"s LIKE 'ee65_{self._run}%' AND p = 'REL_0'",
            force=True,
        )
        assert result["embedded"] >= 1

        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM Graph_KG.kg_EdgeEmbeddings WHERE s LIKE ? AND p != 'REL_0'",
            [f"ee65_{self._run}%"],
        )
        count_other = cursor.fetchone()[0]
        assert count_other == 0

    def test_embed_edges_custom_text_fn(self):
        edges = self._make_nodes_and_edges(3)

        custom_fn = lambda s, p, o: f"{s.lower()} {p.lower()} {o.lower()}"
        result = self.engine.embed_edges(
            where=f"s LIKE 'ee65_{self._run}%'",
            text_fn=custom_fn,
            force=True,
        )
        assert result["embedded"] == 3
        assert result["errors"] == 0

    def test_edge_vector_search_ranking(self):
        edges = self._make_nodes_and_edges(4)

        self.engine.embed_edges(where=f"s LIKE 'ee65_{self._run}%'", force=True)

        query = [0.5, 0.5, 0.5, 0.5]
        results = self.engine.edge_vector_search(query, top_k=4)

        assert len(results) <= 4
        for r in results:
            assert "s" in r and "p" in r and "o_id" in r and "score" in r

        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_edge_vector_search_empty_table(self):
        results = self.engine.edge_vector_search(
            [0.1, 0.2, 0.3, 0.4], top_k=5
        )
        assert results == [] or isinstance(results, list)

    def test_edge_embeddings_round_trip(self):
        s = f"ee65_{self._run}_rt_s"
        o = f"ee65_{self._run}_rt_o"
        p = "associated_with"

        cursor = self.conn.cursor()
        for nid in [s, o]:
            cursor.execute(
                "INSERT INTO Graph_KG.nodes (node_id) SELECT ? WHERE NOT EXISTS "
                "(SELECT 1 FROM Graph_KG.nodes WHERE node_id = ?)",
                [nid, nid],
            )
        cursor.execute(
            "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) SELECT ?, ?, ? WHERE NOT EXISTS "
            "(SELECT 1 FROM Graph_KG.rdf_edges WHERE s=? AND p=? AND o_id=?)",
            [s, p, o, s, p, o],
        )
        self.conn.commit()

        text = f"{s} {p} {o}"
        fixed_emb = [0.1, 0.2, 0.3, 0.4]
        self.engine.embed_text = lambda t: fixed_emb

        self.engine.embed_edges(where=f"s = '{s}'", force=True)

        results = self.engine.edge_vector_search(fixed_emb, top_k=10)
        matching = [r for r in results if r["s"] == s and r["p"] == p and r["o_id"] == o]
        assert len(matching) >= 1
        assert matching[0]["score"] >= 0.99

    def test_embed_edges_text_fn_raises_continues(self):
        edges = self._make_nodes_and_edges(3)
        call_count = [0]

        def sometimes_raises(s, p, o):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("deliberate failure")
            return f"{s} {p} {o}"

        result = self.engine.embed_edges(
            where=f"s LIKE 'ee65_{self._run}%'",
            text_fn=sometimes_raises,
            force=True,
        )

        assert result["errors"] == 1
        assert result["embedded"] == 2
        assert result["total"] == 3

    def test_snapshot_round_trip_edge_embeddings(self, tmp_path):
        edges = self._make_nodes_and_edges(3)
        self.engine.embed_edges(where=f"s LIKE 'ee65_{self._run}%'", force=True)

        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM Graph_KG.kg_EdgeEmbeddings WHERE s LIKE ?",
            [f"ee65_{self._run}%"],
        )
        pre_count = cursor.fetchone()[0]
        assert pre_count == 3

        snap_path = str(tmp_path / "edge_emb_test.ivg")
        self.engine.save_snapshot(snap_path, layers=["sql"])

        cursor.execute(
            "DELETE FROM Graph_KG.kg_EdgeEmbeddings WHERE s LIKE ?",
            [f"ee65_{self._run}%"],
        )
        self.conn.commit()

        cursor.execute(
            "SELECT COUNT(*) FROM Graph_KG.kg_EdgeEmbeddings WHERE s LIKE ?",
            [f"ee65_{self._run}%"],
        )
        assert cursor.fetchone()[0] == 0

        self.engine.restore_snapshot(snap_path, merge=False)

        cursor.execute(
            "SELECT COUNT(*) FROM Graph_KG.kg_EdgeEmbeddings WHERE s LIKE ?",
            [f"ee65_{self._run}%"],
        )
        post_count = cursor.fetchone()[0]
        assert post_count == 3

        results = self.engine.edge_vector_search([0.5, 0.5, 0.5, 0.5], top_k=10)
        matching = [r for r in results if r["s"].startswith(f"ee65_{self._run}")]
        assert len(matching) == 3
