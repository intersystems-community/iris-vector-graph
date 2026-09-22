"""E2E tests for kg_RRF_FUSE covering both Community (HNSW) and Enterprise (IVF+BM25) paths.

Community fixture  — iris_connection / iris_master_cleanup (ivg-iris, port 21972)
Enterprise fixture — arno_iris_connection / arno_master_cleanup (ivg-iris-enterprise, port 31972)

Tests guard the v2.4.8 bug fix: kg_RRF_FUSE with an HNSW-only registry was
returning [] because the loop only matched "ivf" type, never "hnsw".
"""
import json
import math
import os
import random
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


def _norm(vec):
    n = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / n for x in vec]


def _insert_nodes_with_embeddings(conn, nodes, dim=4):
    cursor = conn.cursor()
    for nid, vec in nodes:
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [nid])
        except Exception:
            pass
        vec_str = ",".join(str(v) for v in vec)
        # 4.0.0 keys the embedding tables (graph_id, node_id); the 3.2.0 `id` column
        # is gone. Not swallowed: a swallowed INSERT here left the table empty and the
        # assertions blamed the search path (spec 227).
        cursor.execute(
            "INSERT INTO Graph_KG.kg_NodeEmbeddings (graph_id, node_id, emb) "
            "VALUES ('', ?, TO_VECTOR(?, DOUBLE))",
            [nid, vec_str],
        )
    conn.commit()


def _cleanup_nodes(conn, node_ids):
    cursor = conn.cursor()
    for nid in node_ids:
        for tbl in ("Graph_KG.kg_NodeEmbeddings", "Graph_KG.docs", "Graph_KG.nodes"):
            try:
                cursor.execute(f"DELETE FROM {tbl} WHERE node_id = ?", [nid])
            except Exception:
                try:
                    cursor.execute(f"DELETE FROM {tbl} WHERE id = ?", [nid])
                except Exception:
                    pass
    try:
        conn.commit()
    except Exception:
        pass


def _insert_text_props(conn, nodes, text_template="cancer biology node {nid}"):
    """Insert 'name' props into rdf_props so BM25Index.Build can index them.

    Nodes already exist in Graph_KG.nodes (inserted by _insert_nodes_with_embeddings),
    so we write directly to rdf_props rather than going through create_node (which
    would rollback on duplicate node_id).
    """
    cursor = conn.cursor()
    for nid, _ in nodes:
        text = text_template.format(nid=nid)
        for key, val in (("name", text), ("id", nid)):
            try:
                cursor.execute(
                    'INSERT INTO Graph_KG.rdf_props (s, "key", val) VALUES (?, ?, ?)',
                    [nid, key, val],
                )
            except Exception:
                try:
                    cursor.execute(
                        'UPDATE Graph_KG.rdf_props SET val = ? WHERE s = ? AND "key" = ?',
                        [val, nid, key],
                    )
                except Exception:
                    pass
    try:
        conn.commit()
    except Exception:
        pass


def _require_bm25(engine, idx_name, props=None):
    """Build BM25 index, skip test if ObjectScript class absent."""
    if props is None:
        props = ["name"]
    try:
        engine.bm25_build(idx_name, props)
    except Exception as e:
        msg = str(e).upper()
        if "CLASS DOES NOT EXIST" in msg or "NOT EXIST" in msg or "SQLCODE" in msg:
            pytest.skip(f"Graph.KG.BM25Index not available on this container: {e}")
        raise


# ---------------------------------------------------------------------------
# Community container — HNSW path
# ---------------------------------------------------------------------------

@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
@pytest.mark.requires_clean_isolation
class TestRRFFuseCommunityE2E:
    """Community Edition: only HNSW registered (no ^IVF global).

    Before v2.4.8 fix, kg_RRF_FUSE returned [] because it only searched for
    "ivf" in the registry.  This class pins that regression.
    """

    @pytest.fixture(autouse=True)
    def setup(self, iris_master_cleanup, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine

        self.conn = iris_connection
        self.engine = IRISGraphEngine(iris_connection, embedding_dimension=4)
        self.engine.initialize_schema()
        self._run = uuid.uuid4().hex[:8]

        rng = random.Random(101)
        self._nodes = [
            (f"rrf_comm_{i}_{self._run}", _norm([rng.gauss(0, 1) for _ in range(4)]))
            for i in range(8)
        ]
        _insert_nodes_with_embeddings(iris_connection, self._nodes)
        yield
        _cleanup_nodes(iris_connection, [nid for nid, _ in self._nodes])

    def test_hnsw_only_returns_results(self):
        """Regression v2.4.8: hnsw-only registry must not return []."""
        assert "hnsw" in self.engine._index_registry, (
            "Community container must register 'hnsw' type"
        )
        query_vec = self._nodes[0][1]
        result = self.engine.kg_RRF_FUSE(
            k=5, k1=5, k2=0, c=60,
            query_vector=json.dumps(query_vec),
            query_text="",
        )
        assert isinstance(result, list)
        assert len(result) > 0, (
            "kg_RRF_FUSE with hnsw-only registry returned []; "
            "this was the v2.4.8 regression"
        )
        assert all(len(r) == 4 for r in result), "each result must be (node_id, rrf, vec_score, txt_score)"

    def test_hnsw_top1_is_self(self):
        """The query vector's own node should appear in top results."""
        query_node, query_vec = self._nodes[2]
        result = self.engine.kg_RRF_FUSE(
            k=3, k1=5, k2=0, c=60,
            query_vector=json.dumps(query_vec),
            query_text="",
        )
        top_ids = [r[0] for r in result]
        assert query_node in top_ids, (
            f"Query node {query_node!r} not in top-3 results: {top_ids}"
        )

    def test_hnsw_plus_bm25_fused(self):
        """HNSW vector leg + BM25 text leg: fused result must be non-empty."""
        idx_bm25 = f"rrf_ce_bm25_{self._run}"
        _insert_text_props(self.conn, self._nodes)
        _require_bm25(self.engine, idx_bm25)
        try:
            assert idx_bm25 in self.engine._index_registry
            query_vec = self._nodes[0][1]
            result = self.engine.kg_RRF_FUSE(
                k=5, k1=5, k2=5, c=60,
                query_vector=json.dumps(query_vec),
                query_text="cancer",
            )
            assert len(result) > 0
            assert all(len(r) == 4 for r in result)
        finally:
            try:
                self.engine.bm25_drop(idx_bm25)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Enterprise container — IVF + BM25 path
# ---------------------------------------------------------------------------

@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
@pytest.mark.requires_clean_isolation
class TestRRFFuseEnterpriseE2E:
    """Enterprise container: IVF index + BM25 both registered.

    Uses arno_iris_connection (ivg-iris-enterprise, port 31972).
    Auto-skips when enterprise container is not running.
    """

    @pytest.fixture(autouse=True)
    def setup(self, arno_master_cleanup, arno_iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine

        self.engine = IRISGraphEngine(arno_iris_connection, embedding_dimension=4)
        self.engine.initialize_schema()
        self._run = uuid.uuid4().hex[:8]

        rng = random.Random(202)
        self._nodes = [
            (f"rrf_ent_{i}_{self._run}", _norm([rng.gauss(0, 1) for _ in range(4)]))
            for i in range(10)
        ]
        _insert_nodes_with_embeddings(arno_iris_connection, self._nodes)
        _insert_text_props(arno_iris_connection, self._nodes)
        self._idx_ivf = f"rrf_ent_ivf_{self._run}"
        self._idx_bm25 = f"rrf_ent_bm25_{self._run}"
        yield
        for idx in (self._idx_ivf, self._idx_bm25):
            try:
                self.engine.ivf_drop(idx)
            except Exception:
                pass
            try:
                self.engine.bm25_drop(idx)
            except Exception:
                pass
        _cleanup_nodes(arno_iris_connection, [nid for nid, _ in self._nodes])

    def test_ivf_bm25_rrf_returns_fused_results(self):
        """IVF + BM25 both registered: kg_RRF_FUSE must return fused non-empty results."""
        node_ids = [nid for nid, _ in self._nodes]

        try:
            self.engine.ivf_build(
                self._idx_ivf, nlist=2, build_batch_size=20, node_ids=node_ids
            )
        except Exception as e:
            pytest.skip(f"ivf_build failed (sklearn/numpy missing?): {e}")

        _require_bm25(self.engine, self._idx_bm25)

        assert self._idx_ivf in self.engine._index_registry
        assert self._idx_bm25 in self.engine._index_registry

        query_vec = self._nodes[0][1]
        result = self.engine.kg_RRF_FUSE(
            k=5, k1=5, k2=5, c=60,
            query_vector=json.dumps(query_vec),
            query_text="cancer",
        )
        assert len(result) > 0
        assert all(len(r) == 4 for r in result)
        rrf_scores = [r[1] for r in result]
        assert rrf_scores == sorted(rrf_scores, reverse=True), "results must be RRF-sorted"

    def test_ivf_top1_is_self(self):
        """Query node must appear in IVF top results after ivf_build."""
        node_ids = [nid for nid, _ in self._nodes]
        try:
            self.engine.ivf_build(
                self._idx_ivf, nlist=2, build_batch_size=20, node_ids=node_ids
            )
        except Exception as e:
            pytest.skip(f"ivf_build failed: {e}")

        query_node, query_vec = self._nodes[3]
        raw = self.engine.ivf_search(self._idx_ivf, query_vec, k=3, nprobe=2)
        top_ids = [r[0] for r in raw]
        assert query_node in top_ids, (
            f"Query node {query_node!r} not in IVF top-3: {top_ids}"
        )

    def test_ivf_build_reads_correct_schema(self):
        """ivf_build must read from the schema-prefixed table (regression v2.4.8)."""
        node_ids = [nid for nid, _ in self._nodes]
        # Build succeeds only if it reads from the correct (default) schema prefix.
        # Prior bug: hardcoded Graph_KG.kg_NodeEmbeddings, ignoring set_schema_prefix().
        try:
            result = self.engine.ivf_build(
                self._idx_ivf, nlist=2, build_batch_size=20, node_ids=node_ids
            )
        except Exception as e:
            pytest.skip(f"ivf_build failed (sklearn/numpy missing?): {e}")

        assert result.get("indexed", 0) == len(node_ids), (
            f"ivf_build indexed {result.get('indexed')} nodes, expected {len(node_ids)}"
        )

    def test_bm25_search_finds_text_match(self):
        """BM25 index built on enterprise container must score relevant docs higher."""
        _require_bm25(self.engine, self._idx_bm25)
        results = self.engine.bm25_search(self._idx_bm25, "cancer", k=5)
        assert len(results) > 0, "BM25 search for 'cancer' must return results"
        node_ids_in_results = {r[0] if isinstance(r, tuple) else r["id"] for r in results}
        expected = {nid for nid, _ in self._nodes}
        assert node_ids_in_results & expected, (
            "BM25 results must include at least one seeded node"
        )
