import pytest


def _make_status(**kwargs):
    from iris_vector_graph.status import (
        EngineStatus, TableCounts, AdjacencyStatus,
        ObjectScriptStatus, ArnoStatus, IndexInventory,
    )
    defaults = dict(
        tables=TableCounts(nodes=50_000, edges=220_000, labels=50_000, props=100_000,
                           node_embeddings=50_000, edge_embeddings=0),
        adjacency=AdjacencyStatus(kg_populated=True, kg_edge_count=220_000,
                                  kg_edge_count_capped=False, nkg_populated=True),
        objectscript=ObjectScriptStatus(deployed=True, classes=["Graph.KG.Traversal"]),
        arno=ArnoStatus(loaded=True, capabilities={"bfs": True, "ppr": True}),
        indexes=IndexInventory(hnsw_built=True, ivf_indexes=[],
                               bm25_indexes=[], plaid_indexes=[]),
        embedding_dimension=768,
        probe_ms=15.0,
        errors=[],
        pending_sync=False,
    )
    defaults.update(kwargs)
    return EngineStatus(**defaults)


class TestConceptFirstStatus:

    def test_default_report_no_caret(self):
        s = _make_status()
        report = str(s)
        assert "^" not in report, f"Default status must not contain ^ globals, got:\n{report}"

    def test_default_report_has_concept_labels(self):
        s = _make_status()
        report = str(s)
        assert "Graph:" in report
        assert "Vector index:" in report
        assert "Full-text index:" in report
        assert "Acceleration:" in report

    def test_internals_flag_shows_globals(self):
        s = _make_status()
        report = s.report(internals=True)
        assert "^KG" in report
        assert "^NKG" in report

    def test_internals_flag_false_hides_globals(self):
        s = _make_status()
        assert "^" not in s.report(internals=False)

    def test_str_equals_report_default(self):
        s = _make_status()
        assert str(s) == s.report(internals=False)


class TestIndexStateProperties:

    def test_vector_index_absent_when_no_embeddings(self):
        from iris_vector_graph.status import TableCounts
        s = _make_status(
            tables=TableCounts(nodes=10, edges=5, labels=10, props=20,
                               node_embeddings=0, edge_embeddings=0),
        )
        assert s.vector_index_state == "absent"

    def test_vector_index_empty_when_embeddings_but_no_hnsw(self):
        from iris_vector_graph.status import TableCounts, IndexInventory
        s = _make_status(
            tables=TableCounts(nodes=10, edges=5, labels=10, props=20,
                               node_embeddings=100, edge_embeddings=0),
            indexes=IndexInventory(hnsw_built=False),
        )
        assert s.vector_index_state == "empty"

    def test_vector_index_ready_when_hnsw_built(self):
        from iris_vector_graph.status import IndexInventory
        s = _make_status(indexes=IndexInventory(hnsw_built=True))
        assert s.vector_index_state == "ready"

    def test_fulltext_absent_without_bm25(self):
        from iris_vector_graph.status import IndexInventory
        s = _make_status(indexes=IndexInventory(hnsw_built=True, bm25_indexes=[]))
        assert s.fulltext_index_state == "absent"

    def test_fulltext_ready_with_bm25(self):
        from iris_vector_graph.status import IndexInventory
        s = _make_status(indexes=IndexInventory(hnsw_built=True, bm25_indexes=["my_idx"]))
        assert s.fulltext_index_state == "ready"

    def test_acceleration_ready_when_nkg_populated(self):
        from iris_vector_graph.status import AdjacencyStatus
        s = _make_status(
            adjacency=AdjacencyStatus(kg_populated=True, kg_edge_count=100, nkg_populated=True),
        )
        assert s.acceleration_state == "ready"

    def test_acceleration_empty_when_kg_but_no_nkg(self):
        from iris_vector_graph.status import AdjacencyStatus
        s = _make_status(
            adjacency=AdjacencyStatus(kg_populated=True, kg_edge_count=100, nkg_populated=False),
        )
        assert s.acceleration_state == "empty"

    def test_acceleration_absent_when_neither(self):
        from iris_vector_graph.status import AdjacencyStatus
        s = _make_status(
            adjacency=AdjacencyStatus(kg_populated=False, kg_edge_count=0, nkg_populated=False),
        )
        assert s.acceleration_state == "absent"

    def test_state_values_are_valid(self):
        s = _make_status()
        valid = {"ready", "empty", "building", "absent"}
        assert s.vector_index_state in valid
        assert s.fulltext_index_state in valid
        assert s.acceleration_state in valid


class TestSyncState:

    def test_pending_sync_shown_in_report(self):
        s = _make_status(pending_sync=True)
        report = str(s)
        assert "sync" in report.lower()

    def test_in_sync_shown_in_report(self):
        s = _make_status(pending_sync=False)
        report = str(s)
        assert "sync" in report.lower()

    def test_pending_sync_field_accessible(self):
        s = _make_status(pending_sync=True)
        assert s.pending_sync is True

        s2 = _make_status(pending_sync=False)
        assert s2.pending_sync is False


class TestBiomedicalExample:
    def test_biomed_status_display(self):
        from iris_vector_graph.status import IndexInventory
        s = _make_status(
            indexes=IndexInventory(hnsw_built=True, bm25_indexes=["gene_docs"]),
        )
        report = str(s)
        assert "50,000 nodes" in report
        assert "220,000 edges" in report
        assert "Vector index:    ready" in report
        assert "Full-text index: ready" in report
        assert "Acceleration:    ready" in report
        assert "^" not in report

    def test_internals_shows_for_iris_developer(self):
        s = _make_status()
        report = s.report(internals=True)
        assert "^KG" in report
        assert "^NKG" in report
        assert "Graph.KG.Traversal" in report
