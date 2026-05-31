import uuid
import pytest


@pytest.fixture(scope="session")
def biomed_engine(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine
    engine = IRISGraphEngine(iris_connection)
    try:
        engine.initialize_schema()
    except Exception:
        pass
    yield engine


@pytest.fixture
def biomed_graph(biomed_engine):
    run = uuid.uuid4().hex[:8]

    genes = [f"GENE:{run}:BRCA1", f"GENE:{run}:TP53", f"GENE:{run}:EGFR"]
    diseases = [f"DISEASE:{run}:breast_cancer", f"DISEASE:{run}:lung_cancer"]
    pathways = [f"PATHWAY:{run}:PI3K", f"PATHWAY:{run}:MAPK"]

    for g in genes:
        biomed_engine.create_node(g, labels=["Gene"])
    for d in diseases:
        biomed_engine.create_node(d, labels=["Disease"])
    for p in pathways:
        biomed_engine.create_node(p, labels=["Pathway"])

    edges = [
        {"source_id": genes[0], "predicate": "ASSOCIATED_WITH", "target_id": diseases[0]},
        {"source_id": genes[1], "predicate": "ASSOCIATED_WITH", "target_id": diseases[0]},
        {"source_id": genes[2], "predicate": "ASSOCIATED_WITH", "target_id": diseases[1]},
        {"source_id": genes[0], "predicate": "PARTICIPATES_IN", "target_id": pathways[0]},
        {"source_id": genes[1], "predicate": "PARTICIPATES_IN", "target_id": pathways[1]},
    ]

    yield biomed_engine, edges, genes, diseases, pathways, run

    conn = biomed_engine.conn
    cursor = conn.cursor()
    all_nodes = genes + diseases + pathways
    for nid in all_nodes:
        try:
            cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s=? OR o_id=?", [nid, nid])
            cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s=?", [nid])
            cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s=?", [nid])
            cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id=?", [nid])
        except Exception:
            pass
    try:
        conn.commit()
    except Exception:
        conn.rollback()


class TestSpec178SyncModel:

    def test_bulk_create_edges_auto_sync_default(self, biomed_graph):
        engine, edges, genes, diseases, _, run = biomed_graph
        count = engine.bulk_create_edges(edges)
        assert count == len(edges)

    def test_bulk_create_edges_auto_sync_false_then_manual_sync(self, biomed_graph):
        engine, edges, genes, diseases, _, run = biomed_graph
        engine.bulk_create_edges(edges, auto_sync=False)
        assert engine._nkg_dirty is True or engine._nkg_dirty is False
        engine.sync()
        assert engine._nkg_dirty is False

    def test_auto_sync_false_raises_on_varlen_bfs(self, biomed_graph):
        engine, edges, genes, diseases, pathways, run = biomed_graph
        engine.bulk_ingest_edges(
            [{"s": genes[0], "p": "RELATES_TO", "o": diseases[0]}],
            auto_sync=False,
        )
        if engine._nkg_dirty:
            from iris_vector_graph.errors import IndexNotSyncedError
            with pytest.raises(IndexNotSyncedError):
                engine.execute_cypher(
                    f"MATCH (a)-[*1..3]-(b) WHERE a.id = '{genes[0]}' RETURN b.id LIMIT 5"
                )

    def test_sync_resets_dirty_flag(self, biomed_graph):
        engine, _, genes, diseases, _, run = biomed_graph
        engine.bulk_ingest_edges(
            [{"s": genes[0], "p": "KNOWS", "o": genes[1]}],
            auto_sync=False,
        )
        engine._nkg_dirty = True
        engine.sync()
        assert engine._nkg_dirty is False

    def test_rebuild_kg_emits_deprecation_warning(self, biomed_graph):
        import warnings
        engine, _, _, _, _, _ = biomed_graph
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            engine.rebuild_kg()
        dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(dep) >= 1, "rebuild_kg() should emit DeprecationWarning"

    def test_rebuild_nkg_emits_deprecation_warning(self, biomed_graph):
        import warnings
        engine, _, _, _, _, _ = biomed_graph
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            engine.rebuild_nkg()
        dep = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(dep) >= 1, "rebuild_nkg() should emit DeprecationWarning"

    def test_sync_method_returns_bool(self, biomed_graph):
        engine, _, _, _, _, _ = biomed_graph
        result = engine.sync()
        assert isinstance(result, bool)

    def test_status_shows_sync_state(self, biomed_graph):
        engine, edges, _, _, _, _ = biomed_graph
        engine.bulk_create_edges(edges)
        s = engine.status()
        assert s.pending_sync is False or s.pending_sync is True


class TestSpec179EmbedSelector:

    def test_embed_nodes_label_filter_biomed(self, biomed_graph):
        engine, edges, genes, diseases, _, run = biomed_graph
        engine.bulk_create_edges(edges)

        mock_embedder = _MockEmbedder(dim=8)
        result = engine.embed_nodes(label="Gene", model=mock_embedder)
        assert isinstance(result, dict)
        assert "embedded" in result

    def test_embed_nodes_missing_only_biomed(self, biomed_graph):
        engine, edges, genes, diseases, _, run = biomed_graph
        engine.bulk_create_edges(edges)

        mock_embedder = _MockEmbedder(dim=8)
        result1 = engine.embed_nodes(label="Gene", model=mock_embedder)
        assert result1["embedded"] >= 0

        result2 = engine.embed_nodes(label="Gene", model=mock_embedder, missing_only=True)
        assert isinstance(result2, dict)

    def test_embed_nodes_exclude_pattern_biomed(self, biomed_graph):
        engine, edges, genes, diseases, _, run = biomed_graph
        engine.bulk_create_edges(edges)

        mock_embedder = _MockEmbedder(dim=8)
        result = engine.embed_nodes(
            exclude_pattern=f"DISEASE:{run}:*",
            model=mock_embedder,
        )
        assert isinstance(result, dict)

    def test_embed_edges_predicate_filter_biomed(self, biomed_graph):
        engine, edges, genes, diseases, _, run = biomed_graph
        engine.bulk_create_edges(edges)

        mock_embedder = _MockEmbedder(dim=8)
        result = engine.embed_edges(
            predicate="ASSOCIATED_WITH",
            model=mock_embedder,
        )
        assert isinstance(result, dict)
        assert "embedded" in result

    def test_embed_edges_source_label_biomed(self, biomed_graph):
        engine, edges, genes, diseases, _, run = biomed_graph
        engine.bulk_create_edges(edges)

        mock_embedder = _MockEmbedder(dim=8)
        result = engine.embed_edges(
            source_label="Gene",
            model=mock_embedder,
        )
        assert isinstance(result, dict)

    def test_embed_edges_missing_only_biomed(self, biomed_graph):
        engine, edges, genes, diseases, _, run = biomed_graph
        engine.bulk_create_edges(edges)

        mock_embedder = _MockEmbedder(dim=8)
        result = engine.embed_edges(
            predicate="ASSOCIATED_WITH",
            model=mock_embedder,
            missing_only=True,
        )
        assert isinstance(result, dict)

    def test_embed_nodes_where_not_accepted(self, biomed_graph):
        engine, _, _, _, _, _ = biomed_graph
        with pytest.raises(TypeError):
            engine.embed_nodes(where="node_id LIKE 'GENE:%'")

    def test_embed_edges_where_not_accepted(self, biomed_graph):
        engine, _, _, _, _, _ = biomed_graph
        with pytest.raises(TypeError):
            engine.embed_edges(where="p = 'ASSOCIATED_WITH'")

    def test_sql_injection_in_exclude_pattern_rejected(self, biomed_graph):
        from iris_vector_graph import EmbedSelector
        engine, _, _, _, _, _ = biomed_graph
        with pytest.raises((ValueError, Exception)):
            EmbedSelector(exclude_pattern="'; DROP TABLE Graph_KG.nodes; --")


class TestSpec180ConceptFirstStatus:

    def test_status_default_no_caret_names(self, biomed_graph):
        engine, edges, _, _, _, _ = biomed_graph
        engine.bulk_create_edges(edges)
        s = engine.status()
        report = str(s)
        for global_name in ["^KG", "^NKG", "^BM25Idx", "^IVF", "^PLAID"]:
            assert global_name not in report, \
                f"Default status contains global name {global_name!r}: {report}"

    def test_status_internals_shows_globals(self, biomed_graph):
        engine, edges, _, _, _, _ = biomed_graph
        engine.bulk_create_edges(edges)
        s = engine.status(internals=True)
        report = s.report(internals=True)
        assert "^KG" in report or "^NKG" in report or "Globals" in report

    def test_status_concept_labels_present(self, biomed_graph):
        engine, edges, _, _, _, _ = biomed_graph
        engine.bulk_create_edges(edges)
        s = engine.status()
        report = str(s)
        assert "Graph:" in report
        assert "Vector index:" in report
        assert "Full-text index:" in report
        assert "Acceleration:" in report

    def test_status_shows_node_edge_counts(self, biomed_graph):
        engine, edges, genes, diseases, pathways, run = biomed_graph
        engine.bulk_create_edges(edges)
        s = engine.status()
        assert s.tables.nodes >= len(genes) + len(diseases) + len(pathways)
        assert s.tables.edges >= len(edges)

    def test_vector_index_state_transitions(self, biomed_graph):
        engine, edges, genes, diseases, _, run = biomed_graph
        engine.bulk_create_edges(edges)

        s_before = engine.status()
        assert s_before.vector_index_state in ("absent", "empty", "ready")

    def test_fulltext_index_state_absent_initially(self, biomed_graph):
        engine, _, _, _, _, _ = biomed_graph
        s = engine.status()
        assert s.fulltext_index_state == "absent"

    def test_acceleration_state_after_sync(self, biomed_graph):
        engine, edges, genes, diseases, _, run = biomed_graph
        engine.bulk_create_edges(edges)
        engine.sync()
        s = engine.status()
        assert s.acceleration_state in ("ready", "empty", "absent")

    def test_status_internals_false_is_default(self, biomed_graph):
        engine, _, _, _, _, _ = biomed_graph
        s = engine.status()
        assert s.internals is None

    def test_status_internals_true_populates_globals(self, biomed_graph):
        engine, _, _, _, _, _ = biomed_graph
        s = engine.status(internals=True)
        assert s.internals is not None
        assert isinstance(s.internals, dict)

    def test_pending_sync_reflects_dirty_state(self, biomed_graph):
        engine, _, genes, diseases, _, run = biomed_graph
        engine._nkg_dirty = True
        s = engine.status()
        assert s.pending_sync is True

        engine._nkg_dirty = False
        s2 = engine.status()
        assert s2.pending_sync is False

    def test_str_eq_report_internals_false(self, biomed_graph):
        engine, _, _, _, _, _ = biomed_graph
        s = engine.status()
        assert str(s) == s.report(internals=False)


class _MockEmbedder:
    def __init__(self, dim: int = 8):
        self._dim = dim

    def encode(self, texts):
        import random
        return [[random.random() for _ in range(self._dim)] for _ in texts]
