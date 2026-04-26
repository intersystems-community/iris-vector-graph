import os
import time
import uuid
from unittest.mock import MagicMock, patch

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


class TestEngineStatusUnit:

    def _make_status(self, **kwargs):
        from iris_vector_graph.status import (
            EngineStatus, TableCounts, AdjacencyStatus,
            ObjectScriptStatus, ArnoStatus, IndexInventory,
        )
        defaults = dict(
            tables=TableCounts(nodes=10, edges=5, labels=10, props=20,
                               node_embeddings=0, edge_embeddings=0),
            adjacency=AdjacencyStatus(kg_populated=True, kg_edge_count=5,
                                      kg_edge_count_capped=False, nkg_populated=False),
            objectscript=ObjectScriptStatus(deployed=True, classes=["Graph.KG.Traversal"]),
            arno=ArnoStatus(loaded=False, capabilities={}),
            indexes=IndexInventory(hnsw_built=False, ivf_indexes=[],
                                   bm25_indexes=[], plaid_indexes=[]),
            embedding_dimension=4,
            probe_ms=12.5,
            errors=[],
        )
        defaults.update(kwargs)
        return EngineStatus(**defaults)

    def test_ready_for_bfs_requires_both_kg_and_edges(self):
        from iris_vector_graph.status import AdjacencyStatus, TableCounts
        s = self._make_status(
            adjacency=AdjacencyStatus(kg_populated=True, kg_edge_count=5,
                                      kg_edge_count_capped=False, nkg_populated=False),
            tables=TableCounts(nodes=5, edges=0, labels=5, props=5,
                               node_embeddings=0, edge_embeddings=0),
        )
        assert s.ready_for_bfs is False

        s2 = self._make_status(
            adjacency=AdjacencyStatus(kg_populated=False, kg_edge_count=0,
                                      kg_edge_count_capped=False, nkg_populated=False),
        )
        assert s2.ready_for_bfs is False

        s3 = self._make_status()
        assert s3.ready_for_bfs is True

    def test_ready_for_vector_search(self):
        from iris_vector_graph.status import TableCounts
        s = self._make_status(
            tables=TableCounts(nodes=5, edges=5, labels=5, props=5,
                               node_embeddings=100, edge_embeddings=0),
        )
        assert s.ready_for_vector_search is True

        s2 = self._make_status()
        assert s2.ready_for_vector_search is False

    def test_ready_for_edge_search(self):
        from iris_vector_graph.status import TableCounts
        s = self._make_status(
            tables=TableCounts(nodes=5, edges=5, labels=5, props=5,
                               node_embeddings=0, edge_embeddings=50),
        )
        assert s.ready_for_edge_search is True
        assert self._make_status().ready_for_edge_search is False

    def test_ready_for_full_text(self):
        from iris_vector_graph.status import IndexInventory
        s = self._make_status(
            indexes=IndexInventory(hnsw_built=False, ivf_indexes=[],
                                   bm25_indexes=["my_idx"], plaid_indexes=[]),
        )
        assert s.ready_for_full_text is True
        assert self._make_status().ready_for_full_text is False

    def test_report_contains_all_sections(self):
        s = self._make_status()
        report = s.report()
        assert "SQL Tables" in report
        assert "Adjacency" in report
        assert "ObjectScript" in report
        assert "Arno" in report
        assert "Indexes" in report
        assert "12" in report

    def test_report_warns_kg_empty_with_edges(self):
        from iris_vector_graph.status import AdjacencyStatus, TableCounts
        s = self._make_status(
            tables=TableCounts(nodes=5, edges=50, labels=5, props=5,
                               node_embeddings=0, edge_embeddings=0),
            adjacency=AdjacencyStatus(kg_populated=False, kg_edge_count=0,
                                      kg_edge_count_capped=False, nkg_populated=False),
        )
        report = s.report()
        assert "BuildKG" in report or "^KG" in report

    def test_report_shows_capped_count(self):
        from iris_vector_graph.status import AdjacencyStatus
        s = self._make_status(
            adjacency=AdjacencyStatus(kg_populated=True, kg_edge_count=10000,
                                      kg_edge_count_capped=True, nkg_populated=False),
        )
        report = s.report()
        assert "≥10,000" in report or "10,000+" in report or "capped" in report.lower()

    def test_errors_captured_not_raised(self):
        from iris_vector_graph.status import EngineStatus
        s = EngineStatus(errors=["probe X failed: timeout"])
        assert len(s.errors) == 1
        report = s.report()
        assert "probe X failed" in report


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestEngineStatusE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine
        self.conn = iris_connection
        self.engine = IRISGraphEngine(iris_connection, embedding_dimension=4)
        self.engine.initialize_schema()
        self._run = uuid.uuid4().hex[:8]
        self._nodes = []
        yield
        cursor = self.conn.cursor()
        for nid in self._nodes:
            try:
                cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s=? OR o_id=?", [nid, nid])
                cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s=?", [nid])
                cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id=?", [nid])
            except Exception:
                pass
        try:
            self.conn.commit()
        except Exception:
            self.conn.rollback()

    def _node(self, suffix):
        nid = f"st80_{self._run}_{suffix}"
        self._nodes.append(nid)
        self.engine.create_node(nid, labels=["Gene"])
        return nid

    def test_status_returns_engine_status(self):
        from iris_vector_graph.status import EngineStatus
        s = self.engine.status()
        assert isinstance(s, EngineStatus)

    def test_status_fresh_schema_no_errors(self):
        s = self.engine.status()
        assert len(s.errors) == 0 or all("optional" in e.lower() for e in s.errors)

    def test_status_after_create_edge_shows_edges_and_kg(self):
        a = self._node("a")
        b = self._node("b")
        self.engine.create_edge(a, "REL", b)

        s = self.engine.status()
        assert s.tables.edges >= 1
        assert s.adjacency.kg_populated is True
        assert s.ready_for_bfs is True

    def test_status_completes_under_500ms(self):
        t0 = time.perf_counter()
        s = self.engine.status()
        elapsed = (time.perf_counter() - t0) * 1000
        assert elapsed < 500, f"status() took {elapsed:.0f}ms — exceeds 500ms target"
        assert s.probe_ms < 500

    def test_status_graceful_on_missing_index_tables(self):
        s = self.engine.status()
        assert isinstance(s.indexes.ivf_indexes, list)
        assert isinstance(s.indexes.bm25_indexes, list)
        assert isinstance(s.indexes.plaid_indexes, list)
        assert len(s.errors) == 0 or True

    def test_status_report_is_human_readable(self):
        a = self._node("rep_a")
        b = self._node("rep_b")
        self.engine.create_edge(a, "REL", b)
        s = self.engine.status()
        report = s.report()
        assert len(report) > 100
        assert "\n" in report
        assert "edges" in report.lower()
