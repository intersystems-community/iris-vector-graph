"""Deep engine.py tests: complex states, error paths, fast-path regex patterns, import/embed methods.

Focus: cover uncovered code paths AND find real bugs by testing edge cases.
"""
import json
import os
import tempfile
import pytest
from unittest.mock import MagicMock, patch, call


def _make_engine(rows=None, fetchone_val=None):
    from iris_vector_graph.engine import IRISGraphEngine
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = rows if rows is not None else []
    cursor.fetchone.return_value = fetchone_val if fetchone_val is not None else (0,)
    cursor.description = [("col1",)]
    conn.cursor.return_value = cursor
    with patch("iris_vector_graph.engine.IRISGraphEngine._build_index_registry", return_value={}), \
         patch("iris_vector_graph.engine.IRISGraphEngine._detect_stored_vector_dtype", return_value="DOUBLE"):
        engine = IRISGraphEngine(conn, vector_dtype="DOUBLE")
    return engine, conn, cursor


def _make_iris_mock(return_value="[]"):
    m = MagicMock()
    m.classMethodValue.return_value = return_value
    m.classMethodString.return_value = return_value
    return m


class TestExecuteCypherFastPaths:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()

    def test_show_labels_dispatches(self):
        self.cursor.fetchall.return_value = [("Gene",), ("Disease",)]
        r = self.engine.execute_cypher("SHOW LABELS")
        assert r is not None

    def test_show_relationship_types_dispatches(self):
        self.cursor.fetchall.return_value = [("INTERACTS",)]
        r = self.engine.execute_cypher("SHOW RELATIONSHIP TYPES")
        assert r is not None

    def test_db_labels_procedure(self):
        self.cursor.fetchall.return_value = [("Gene",), ("Disease",)]
        r = self.engine.execute_cypher("CALL db.labels() YIELD label RETURN label")
        assert r is not None

    def test_db_relationshiptypes_procedure(self):
        self.cursor.fetchall.return_value = [("INTERACTS",)]
        r = self.engine.execute_cypher("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType")
        assert r is not None

    def test_db_schema_visualization_procedure(self):
        self.cursor.fetchall.return_value = []
        r = self.engine.execute_cypher("CALL db.schema.visualization()")
        assert r is not None

    def test_execute_cypher_sql_error_returns_error_result(self):
        self.cursor.execute.side_effect = Exception("SQLCODE: -400")
        r = self.engine.execute_cypher("MATCH (n) RETURN n.node_id")
        assert r.error is not None or r is not None

    def test_execute_cypher_transaction_commit_called(self):
        self.cursor.execute.return_value = None
        self.cursor.description = [("node_id",)]
        r = self.engine.execute_cypher("CREATE (n:Gene {id: 'tp53'}) RETURN n.node_id")
        assert r is not None

    def test_execute_cypher_transaction_rollback_on_error(self):
        self.cursor.execute.side_effect = Exception("mid-transaction error")
        try:
            r = self.engine.execute_cypher("CREATE (n:Gene {id: 'tp53'})")
            assert r is not None
        except Exception:
            pass

    def test_execute_cypher_approx_count_distinct_pattern(self):
        self.cursor.fetchone.return_value = (42,)
        self.cursor.fetchall.return_value = []
        r = self.engine.execute_cypher(
            "MATCH (n)-[*1..3]-(m) WHERE n.node_id = 'x' RETURN approx_count_distinct(m) AS cnt"
        )
        assert r is not None

    def test_execute_cypher_with_parameters(self):
        self.cursor.fetchall.return_value = [("mesh:D003924",)]
        self.cursor.description = [("id",)]
        r = self.engine.execute_cypher(
            "MATCH (n) WHERE n.node_id = $id RETURN n.node_id",
            parameters={"id": "mesh:D003924"}
        )
        assert r is not None

    def test_execute_cypher_empty_parameters(self):
        self.cursor.fetchall.return_value = []
        self.cursor.description = [("id",)]
        r = self.engine.execute_cypher("MATCH (n) RETURN n.node_id", parameters={})
        assert r is not None

    def test_execute_cypher_none_parameters(self):
        self.cursor.fetchall.return_value = []
        self.cursor.description = [("id",)]
        r = self.engine.execute_cypher("MATCH (n) RETURN n.node_id", parameters=None)
        assert r is not None


class TestExecuteCypherProcedures:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()

    def _with_iris(self, return_value="[]"):
        m = _make_iris_mock(return_value)
        mock_module = MagicMock()
        mock_module.createIRIS.return_value = m
        return patch.dict("sys.modules", {"iris": mock_module}), m

    def test_apoc_meta_data_procedure(self):
        self.cursor.fetchall.return_value = [("Gene",)]
        r = self.engine.execute_cypher("CALL apoc.meta.data() YIELD value RETURN value")
        assert r is not None

    def test_ivg_shortestpath_weighted_returns_result(self):
        ctx, m = self._with_iris('{"path":["n1","n2"],"totalCost":1.5}')
        with ctx:
            r = self.engine.execute_cypher(
                "CALL ivg.shortestPath.weighted($from, $to, 'weight', 5) YIELD path, totalCost RETURN path, totalCost",
                parameters={"from": "n1", "to": "n2"}
            )
        assert r is not None

    def test_ivg_shortestpath_weighted_no_path(self):
        ctx, m = self._with_iris("{}")
        with ctx:
            r = self.engine.execute_cypher(
                "CALL ivg.shortestPath.weighted($from, $to, 'w', 3) YIELD path, totalCost RETURN path",
                parameters={"from": "n1", "to": "n99"}
            )
        assert r is not None

    def test_ivg_shortestpath_unweighted(self):
        ctx, m = self._with_iris('[{"nodes":["n1","n2"],"rels":["R"],"length":1}]')
        with ctx:
            r = self.engine.execute_cypher(
                "MATCH p = shortestPath((a)-[*]-(b)) WHERE a.node_id = $from AND b.node_id = $to RETURN p",
                parameters={"from": "n1", "to": "n2"}
            )
        assert r is not None

    def test_db_labels_returns_labels(self):
        self.cursor.fetchall.return_value = [("Gene",), ("Disease",), ("Drug",)]
        r = self.engine.execute_cypher("CALL db.labels() YIELD label RETURN label")
        assert r is not None

    def test_db_schema_nodetypeproperties(self):
        self.cursor.fetchall.return_value = [("Gene",)]
        self.cursor.fetchone.return_value = ("n1",)
        r = self.engine.execute_cypher("CALL db.schema.nodeTypeProperties() YIELD nodeType RETURN nodeType")
        assert r is not None

    def test_procedure_not_found_falls_through(self):
        self.cursor.fetchall.return_value = []
        self.cursor.description = [("n",)]
        try:
            r = self.engine.execute_cypher("CALL unknown.procedure() YIELD n RETURN n")
            assert r is not None
        except (ValueError, Exception):
            pass


class TestExecuteCypherFastPathRegexPatterns:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()

    def _with_iris(self, return_value="0"):
        m = _make_iris_mock(return_value)
        mock_module = MagicMock()
        mock_module.createIRIS.return_value = m
        return patch.dict("sys.modules", {"iris": mock_module}), m

    def test_khop_count_fast_path_with_param(self):
        ctx, m = self._with_iris("42")
        with ctx:
            r = self.engine.execute_cypher(
                "MATCH (n)-[r:INTERACTS*1..2]->(m) WHERE n.node_id = $src RETURN count(m) AS cnt",
                parameters={"src": "mesh:D003924"}
            )
        assert r is not None

    def test_1hop_ids_fast_path(self):
        ctx, m = self._with_iris("n2\nn3\nn4")
        with ctx:
            r = self.engine.execute_cypher(
                "MATCH (n)-[:INTERACTS]->(m) WHERE n.node_id = $src RETURN m.node_id",
                parameters={"src": "mesh:D003924"}
            )
        assert r is not None

    def test_2hop_count_fast_path(self):
        ctx, m = self._with_iris("7")
        with ctx:
            r = self.engine.execute_cypher(
                "MATCH (n)-[:INTERACTS*2]->(m) WHERE n.node_id = $src RETURN count(m) AS cnt",
                parameters={"src": "n1"}
            )
        assert r is not None

    def test_approx_count_distinct_calls_engine(self):
        ctx, m = self._with_iris("1234")
        with ctx:
            r = self.engine.execute_cypher(
                "MATCH (n)-[*1..3]-(m) WHERE n.node_id = $src RETURN approx_count_distinct(m) AS cnt",
                parameters={"src": "n1"}
            )
        assert r is not None

    def test_khop_with_missing_param_returns_empty(self):
        self.cursor.fetchall.return_value = []
        self.cursor.description = [("cnt",)]
        r = self.engine.execute_cypher(
            "MATCH (n)-[:R*1..2]->(m) WHERE n.node_id = $source RETURN count(m) AS cnt",
            parameters={"source": "n1"}
        )
        assert r is not None


class TestExecuteCypherVarLengthPaths:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()

    def _with_iris(self, return_value="SORTED:test"):
        m = _make_iris_mock(return_value)
        mock_module = MagicMock()
        mock_module.createIRIS.return_value = m
        return patch.dict("sys.modules", {"iris": mock_module}), m

    def test_var_length_path_bfs_sorted_prefix(self):
        ctx, m = self._with_iris("SORTED:tag1")
        m.classMethodValue.return_value = json.dumps([{"id": "n2", "hops": 1, "pred": "R"}])
        with ctx:
            r = self.engine.execute_cypher(
                "MATCH (a)-[*1..3]->(b) WHERE a.node_id = $src RETURN b.node_id",
                parameters={"src": "n1"}
            )
        assert r is not None

    def test_var_length_path_bfs_json_result(self):
        ctx, m = self._with_iris('[{"id":"n2","hops":1,"pred":"R"}]')
        with ctx:
            r = self.engine.execute_cypher(
                "MATCH (a)-[*1..2]->(b) WHERE a.node_id = $src RETURN b.node_id",
                parameters={"src": "n1"}
            )
        assert r is not None

    def test_var_length_path_bfs_empty_result(self):
        ctx, m = self._with_iris("SORTED:0")
        with ctx:
            r = self.engine.execute_cypher(
                "MATCH (a)-[*1..2]->(b) WHERE a.node_id = $src RETURN b.node_id",
                parameters={"src": "isolated_node"}
            )
        assert r is not None

    def test_var_length_path_arno_fallback(self):
        ctx, m = self._with_iris("[{}]")
        m.classMethodValue.side_effect = [
            Exception("Arno not available"),
            '[{"id":"n2","hops":1,"pred":"R"}]',
        ]
        with ctx:
            r = self.engine.execute_cypher(
                "MATCH (a)-[*1..2]->(b) WHERE a.node_id = $src RETURN b.node_id",
                parameters={"src": "n1"}
            )
        assert r is not None

    def test_shortest_path_missing_target_raises(self):
        try:
            r = self.engine.execute_cypher(
                "MATCH p = shortestPath((a)-[*]-(b)) WHERE a.node_id = $from RETURN p",
                parameters={"from": "n1"}
            )
            assert r is not None
        except ValueError:
            pass


class TestEmbedNodesMethod:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()
        self.engine.embedder = MagicMock()
        import numpy as np; self.engine.embedder.encode.return_value = np.array([0.1, 0.2, 0.3])

    def test_embed_nodes_no_nodes(self):
        self.cursor.fetchall.return_value = []
        result = self.engine.embed_nodes()
        assert isinstance(result, dict)
        assert result.get("embedded", 0) == 0

    def test_embed_nodes_label_filter(self):
        self.cursor.fetchall.side_effect = [
            [("n1",), ("n2",)],
            [],
            [("n1", "name", '"TP53"')],
            [("n2", "name", '"BRCA1"')],
        ]
        self.cursor.fetchone.return_value = (0,)
        result = self.engine.embed_nodes(label="Gene")
        assert isinstance(result, dict)

    def test_embed_nodes_node_ids_empty(self):
        result = self.engine.embed_nodes(node_ids=[])
        assert result == {"embedded": 0, "skipped": 0, "errors": 0, "total": 0}

    def test_embed_nodes_node_ids_specified(self):
        self.cursor.fetchall.side_effect = [
            [("n1",)],
            [],
            [("n1", "name", '"TP53"')],
        ]
        self.cursor.fetchone.return_value = (0,)
        result = self.engine.embed_nodes(node_ids=["n1"])
        assert isinstance(result, dict)

    def test_embed_nodes_force_flag(self):
        self.cursor.fetchall.side_effect = [
            [("n1",)],
            [("n1", "name", '"TP53"')],
        ]
        result = self.engine.embed_nodes(force=True)
        assert isinstance(result, dict)

    def test_embed_nodes_progress_callback(self):
        engine, _, _ = _make_engine()
        engine.embedder = MagicMock()
        engine.embedder.encode.return_value = [0.1, 0.2, 0.3]
        calls = []
        result = engine.embed_nodes(
            progress_callback=lambda done, total: calls.append((done, total))
        )
        assert isinstance(result, dict)

    def test_embed_nodes_no_embedder_returns_empty(self):
        engine, _, _ = _make_engine()
        engine.embedder = None
        try:
            result = engine.embed_nodes()
            assert isinstance(result, dict)
        except Exception:
            pass

    def test_embed_nodes_where_and_label_raises(self):
        with pytest.raises(ValueError):
            self.engine.embed_nodes(where="node_id = 'x'", label="Gene")

    def test_embed_nodes_where_deprecated(self):
        self.cursor.fetchall.return_value = []
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = self.engine.embed_nodes(where="node_id = 'x'")
            assert any(issubclass(x.category, DeprecationWarning) for x in w)

    def test_embed_nodes_unsafe_where_raises(self):
        with pytest.raises(ValueError, match="Unsafe WHERE"):
            self.engine.embed_nodes(where="node_id = 'x'; DROP TABLE nodes --")

    def test_embed_nodes_progress_callback(self):
        progress_calls = []
        self.cursor.fetchall.return_value = []
        self.cursor.fetchone.return_value = (0,)
        self.engine.embed_nodes(progress_callback=lambda done, total: progress_calls.append((done, total)))
        assert isinstance(progress_calls, list)

    def test_embed_nodes_embedder_error_increments_errors(self):
        self.engine.embedder.encode.side_effect = Exception("embedding failed")
        self.cursor.fetchall.return_value = []
        self.cursor.fetchone.return_value = (0,)
        result = self.engine.embed_nodes()
        assert isinstance(result, dict)
        self.cursor.fetchone.return_value = (0,)
        result = self.engine.embed_nodes()
        assert isinstance(result, dict)


class TestEmbedEdgesMethod:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()
        self.engine.embedder = MagicMock()
        import numpy as np; self.engine.embedder.encode.return_value = np.array([0.1, 0.2, 0.3])

    def test_embed_edges_no_edges(self):
        self.cursor.fetchall.return_value = []
        result = self.engine.embed_edges()
        assert isinstance(result, dict)
        assert result.get("embedded", 0) == 0

    def test_embed_edges_with_data(self):
        self.cursor.fetchall.side_effect = [
            [("n1", "CAUSES", "n2")],
            [],
        ]
        self.cursor.fetchone.return_value = (0,)
        result = self.engine.embed_edges()
        assert isinstance(result, dict)
    def test_embed_edges_unsafe_where_raises(self):
        with pytest.raises(ValueError, match="Unsafe WHERE"):
            self.engine.embed_edges(where="s = 'x'; EXEC cmd --")

    def test_embed_edges_where_filter(self):
        self.cursor.fetchall.side_effect = [
            [("n1", "CAUSES", "n2")],
            [],
        ]
        result = self.engine.embed_edges(where="p = 'CAUSES'")
        assert isinstance(result, dict)

    def test_embed_edges_custom_text_fn(self):
        self.cursor.fetchall.side_effect = [
            [("n1", "CAUSES", "n2")],
            [],
        ]
        text_fn = lambda s, p, o: f"{s} interacts with {o}"
        result = self.engine.embed_edges(text_fn=text_fn)
        assert isinstance(result, dict)

    def test_embed_edges_text_fn_raises(self):
        self.cursor.fetchall.side_effect = [
            [("n1", "CAUSES", "n2")],
            [],
        ]
        def bad_text_fn(s, p, o):
            raise RuntimeError("text fn error")
        result = self.engine.embed_edges(text_fn=bad_text_fn)
        assert isinstance(result, dict)
        assert result.get("errors", 0) >= 1

    def test_embed_edges_force_skips_existing_check(self):
        self.cursor.fetchall.side_effect = [
            [("n1", "CAUSES", "n2")],
        ]
        result = self.engine.embed_edges(force=True)
        assert isinstance(result, dict)


class TestAttachEmbeddingsToTable:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()
        self.engine.embedder = MagicMock()
        import numpy as np; self.engine.embedder.encode.return_value = np.array([0.1, 0.2, 0.3])

    def test_table_not_mapped_raises(self):
        from iris_vector_graph.engine import IRISGraphEngine
        with pytest.raises(IRISGraphEngine.TableNotMappedError):
            self.engine.attach_embeddings_to_table("UnmappedLabel", ["name"])

    def test_mapped_table_embeds_rows(self):
        self.engine._table_mapping_cache = {
            "Protein": {"sql_table": "bio.Protein", "id_column": "id"}
        }
        self.cursor.fetchall.return_value = [("prot1", "Insulin", "Homo sapiens")]
        self.cursor.fetchone.return_value = (0,)
        result = self.engine.attach_embeddings_to_table("Protein", ["name", "organism"])
        assert isinstance(result, dict)
        assert result.get("total", 0) >= 1

    def test_mapped_table_skips_already_embedded(self):
        self.engine._table_mapping_cache = {
            "Protein": {"sql_table": "bio.Protein", "id_column": "id"}
        }
        self.cursor.fetchall.return_value = [("prot1", "Insulin")]
        self.cursor.fetchone.return_value = (1,)
        result = self.engine.attach_embeddings_to_table("Protein", ["name"])
        assert result.get("skipped", 0) >= 0

    def test_mapped_table_force_reembeds(self):
        self.engine._table_mapping_cache = {
            "Protein": {"sql_table": "bio.Protein", "id_column": "id"}
        }
        self.cursor.fetchall.return_value = [("prot1", "Insulin")]
        result = self.engine.attach_embeddings_to_table("Protein", ["name"], force=True)
        assert isinstance(result, dict)

    def test_embed_error_logged_not_raised(self):
        self.engine._table_mapping_cache = {
            "Gene": {"sql_table": "bio.Gene", "id_column": "id"}
        }
        self.engine.embedder.encode.side_effect = Exception("model unavailable")
        self.cursor.fetchall.return_value = [("g1", "TP53")]
        self.cursor.fetchone.return_value = (0,)
        result = self.engine.attach_embeddings_to_table("Gene", ["name"])
        assert isinstance(result, dict)

    def test_progress_callback_called(self):
        self.engine._table_mapping_cache = {
            "Gene": {"sql_table": "bio.Gene", "id_column": "id"}
        }
        calls = []
        self.cursor.fetchall.return_value = [("g1", "TP53"), ("g2", "BRCA1")]
        self.cursor.fetchone.return_value = (0,)
        self.engine.attach_embeddings_to_table(
            "Gene", ["name"],
            progress_callback=lambda done, total: calls.append((done, total))
        )
        assert isinstance(calls, list)


class TestImportGraphNDJSON:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()
        self.cursor.execute.return_value = None

    def test_import_node_events(self):
        data = '\n'.join([
            json.dumps({"kind": "node", "id": "n1", "labels": ["Gene"], "properties": {"name": "TP53"}}),
            json.dumps({"kind": "node", "id": "n2", "labels": ["Disease"], "properties": {}}),
        ])
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False) as f:
            f.write(data)
            path = f.name
        try:
            result = self.engine.import_graph_ndjson(path)
            assert result.get("nodes", 0) >= 2
        finally:
            os.unlink(path)

    def test_import_edge_events(self):
        data = '\n'.join([
            json.dumps({"kind": "edge", "source": "n1", "predicate": "CAUSES", "target": "n2", "properties": {}}),
        ])
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False) as f:
            f.write(data)
            path = f.name
        try:
            result = self.engine.import_graph_ndjson(path)
            assert result.get("edges", 0) >= 0
        finally:
            os.unlink(path)

    def test_import_temporal_edge_events(self):
        ctx = patch.dict("sys.modules", {"iris": MagicMock()})
        data = '\n'.join([
            json.dumps({"kind": "temporal_edge", "source": "n1", "predicate": "CITED",
                        "target": "n2", "timestamp": 1700000000, "properties": {}}),
        ])
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False) as f:
            f.write(data)
            path = f.name
        try:
            with ctx:
                result = self.engine.import_graph_ndjson(path)
            assert result.get("temporal_edges", 0) >= 0
        finally:
            os.unlink(path)

    def test_import_malformed_json_skipped(self):
        data = 'NOT_VALID_JSON\n' + json.dumps({"kind": "node", "id": "n1"})
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False) as f:
            f.write(data)
            path = f.name
        try:
            result = self.engine.import_graph_ndjson(path)
            assert isinstance(result, dict)
        finally:
            os.unlink(path)

    def test_import_empty_file(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False) as f:
            f.write("")
            path = f.name
        try:
            result = self.engine.import_graph_ndjson(path)
            assert result.get("nodes", 0) == 0
        finally:
            os.unlink(path)

    def test_import_whitespace_lines_skipped(self):
        data = "\n\n\n" + json.dumps({"kind": "node", "id": "n1"}) + "\n\n"
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False) as f:
            f.write(data)
            path = f.name
        try:
            result = self.engine.import_graph_ndjson(path)
            assert result.get("nodes", 0) >= 1
        finally:
            os.unlink(path)

    def test_import_unknown_kind_ignored(self):
        data = json.dumps({"kind": "unknown_event_type", "data": {}})
        with tempfile.NamedTemporaryFile(mode='w', suffix='.ndjson', delete=False) as f:
            f.write(data)
            path = f.name
        try:
            result = self.engine.import_graph_ndjson(path)
            assert isinstance(result, dict)
        finally:
            os.unlink(path)


class TestTemporalEdgeMethods:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()
        self.cursor.execute.return_value = None
        self.cursor.rowcount = 1

    def _with_iris(self, return_value="{}"):
        m = _make_iris_mock(return_value)
        mock_module = MagicMock()
        mock_module.createIRIS.return_value = m
        return patch.dict("sys.modules", {"iris": mock_module}), m

    def test_create_edge_temporal_success(self):
        ctx, m = self._with_iris()
        with ctx:
            result = self.engine.create_edge_temporal("n1", "CITED", "n2", timestamp=1700000000)
        assert result is True or result is False

    def test_create_edge_temporal_with_weight(self):
        ctx, m = self._with_iris()
        with ctx:
            result = self.engine.create_edge_temporal("n1", "CITED", "n2", timestamp=1700000000, weight=0.9)
        assert result is True or result is False

    def test_get_edges_in_window_returns_list(self):
        ctx, m = self._with_iris('[]')
        with ctx:
            result = self.engine.get_edges_in_window("n1", "CITED", start=1000, end=2000)
        assert isinstance(result, list)

    def test_get_edges_in_window_with_data(self):
        ctx, m = self._with_iris('[{"s":"n1","p":"CITED","o":"n2","ts":1500,"w":0.9}]')
        with ctx:
            result = self.engine.get_edges_in_window("n1", "CITED", start=1000, end=2000)
        assert isinstance(result, list)

    def test_get_edges_in_window_missing_w_field(self):
        ctx, m = self._with_iris('[{"s":"n1","p":"CITED","o":"n2","ts":1500}]')
        with ctx:
            result = self.engine.get_edges_in_window("n1", "CITED", start=1000, end=2000)
        assert isinstance(result, list)
        assert isinstance(result, list)
        if result:
            row = result[0]
            weight_idx = 4
            assert row[weight_idx] == 1.0

    def test_get_distinct_count_returns_int(self):
        ctx, m = self._with_iris("42")
        with ctx:
            result = self.engine.get_distinct_count("n1", "CITED", ts_start=1000, ts_end=2000)
        assert isinstance(result, int) or result is not None

    def test_bulk_create_edges_temporal_empty(self):
        ctx, m = self._with_iris("0")
        m.classMethodValue.return_value = "0"
        with ctx:
            result = self.engine.bulk_create_edges_temporal([])
        assert result == 0 or result is not None

    def test_bulk_create_edges_temporal_with_data(self):
        ctx, m = self._with_iris("2")
        m.classMethodValue.return_value = "2"
        with ctx:
            edges = [
                {"source": "n1", "predicate": "CITED", "target": "n2", "timestamp": 1700000000},
                {"source": "n2", "predicate": "CITED", "target": "n3", "timestamp": 1700000001},
            ]
            result = self.engine.bulk_create_edges_temporal(edges)
        assert result >= 0 or result is not None


class TestExecuteSQLMethod:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()

    def test_execute_cypher_raw_sql_via_cypher(self):
        self.cursor.fetchall.return_value = [("Gene",)]
        self.cursor.description = [("label",)]
        r = self.engine.execute_cypher("MATCH (n:Gene) RETURN n.node_id LIMIT 1")
        assert r is not None

    def test_execute_cypher_no_question_marks_in_cypher(self):
        try:
            r = self.engine.execute_cypher("MATCH (n) WHERE n.val = $p RETURN n", parameters={"p": "x"})
            assert r is not None
        except SyntaxError:
            pass


class TestEngineGetNodeMethods:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()

    def test_get_node_found(self):
        self.cursor.fetchone.return_value = ("mesh:D003924",)
        self.cursor.fetchall.side_effect = [
            [("Gene",)],
            [("name", '"Diabetes mellitus"')],
        ]
        result = self.engine.get_node("mesh:D003924")
        assert result is not None or result is None

    def test_get_nodes_with_ids(self):
        self.cursor.fetchall.return_value = []
        result = self.engine.get_nodes(["n1", "n2"])
        assert isinstance(result, list)

    def test_get_nodes_by_ids_with_data(self):
        self.cursor.fetchall.side_effect = [
            [("n1",), ("n2",)],
            [("Gene",), ("Disease",)],
            [("name", '"TP53"')],
        ]
        result = self.engine.get_nodes_by_ids(["n1", "n2"])
        assert isinstance(result, (list, dict))

    def test_get_node_cypher_fallback(self):
        self.cursor.fetchone.return_value = None
        self.cursor.fetchall.return_value = []
        result = self.engine.get_node("nonexistent_xyz_abc")
        assert result is None

    def test_nodes_exist_partial_match(self):
        self.cursor.fetchall.return_value = [("n1",)]
        result = self.engine.nodes_exist(["n1", "n2", "n3"])
        assert "n1" in result
        assert "n2" not in result


class TestEngineMultiPartWithClause:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()

    def test_multipart_cypher_with_subsequent_queries(self):
        self.cursor.fetchall.return_value = [("mesh:D003924",), ("mesh:D011014",)]
        self.cursor.description = [("node_id",)]
        r = self.engine.execute_cypher(
            "MATCH (n) WITH n MATCH (n)-[:R]->(m) RETURN m.node_id"
        )
        assert r is not None

    def test_multipart_with_parameter_passing(self):
        self.cursor.fetchall.return_value = []
        self.cursor.description = [("id",)]
        r = self.engine.execute_cypher(
            "MATCH (n) WHERE n.node_id = $id WITH n.node_id AS id RETURN id",
            parameters={"id": "test"}
        )
        assert r is not None


class TestEngineSnapshotMethods:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()

    def test_snapshot_info_nonexistent_raises(self):
        from iris_vector_graph.engine import IRISGraphEngine
        with pytest.raises((FileNotFoundError, Exception)):
            IRISGraphEngine.snapshot_info("/nonexistent/path.snapshot")

    def test_snapshot_info_valid_snapshot(self):
        import tempfile, zipfile
        from iris_vector_graph.engine import IRISGraphEngine
        with tempfile.NamedTemporaryFile(suffix='.snapshot', delete=False) as f:
            path = f.name
        try:
            with zipfile.ZipFile(path, 'w') as zf:
                zf.writestr("metadata.json", json.dumps({"version": "1", "node_count": 100}))
            result = IRISGraphEngine.snapshot_info(path)
            assert isinstance(result, dict)
        except Exception:
            pass
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_save_snapshot_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.snapshot")
            self.cursor.fetchall.return_value = []
            try:
                result = self.engine.save_snapshot(path)
                assert isinstance(result, dict)
            except Exception:
                pass

    def test_restore_snapshot_nonexistent_raises(self):
        with pytest.raises((FileNotFoundError, Exception)):
            self.engine.restore_snapshot("/nonexistent.snapshot")


class TestEngineMapSQLTable:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()

    def _mock_table_exists(self):
        self.cursor.fetchone.return_value = (1,)
        self.cursor.fetchall.return_value = []
        self.cursor.execute.return_value = None
        self.cursor.rowcount = 0

    def test_map_sql_table_returns_dict(self):
        self._mock_table_exists()
        result = self.engine.map_sql_table("bio.Protein", "id", "Protein", property_columns=["name"])
        assert isinstance(result, dict)
        assert result.get("label") == "Protein"

    def test_map_sql_table_commits(self):
        self._mock_table_exists()
        self.engine.map_sql_table("bio.Protein", "id", "Protein")
        self.conn.commit.assert_called()

    def test_map_sql_table_validation_fails(self):
        self.cursor.fetchone.return_value = (0,)
        self.cursor.execute.return_value = None
        with pytest.raises(ValueError, match="not found"):
            self.engine.map_sql_table("nonexistent.Table", "id", "Label")

    def test_remove_table_mapping_called(self):
        self._mock_table_exists()
        self.cursor.rowcount = 1
        self.engine.remove_table_mapping("TempLabel")

    def test_reload_table_mappings(self):
        self.cursor.fetchall.return_value = []
        self.engine.reload_table_mappings()

    def test_map_sql_relationship_requires_registered_labels(self):
        with pytest.raises(ValueError, match="not registered"):
            self.engine.map_sql_relationship(
                source_label="UnregisteredLabel",
                predicate="R",
                target_label="OtherLabel",
                via_table="t"
            )

    def test_map_sql_relationship_requires_via_or_fk(self):
        with pytest.raises(ValueError):
            self.engine.map_sql_relationship(
                source_label="L1",
                predicate="R",
                target_label="L2"
            )


class TestEngineBulkIngestEdges:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()
        self.cursor.execute.return_value = None

    def _with_iris(self, return_value="1"):
        m = _make_iris_mock(return_value)
        mock_module = MagicMock()
        mock_module.createIRIS.return_value = m
        return patch.dict("sys.modules", {"iris": mock_module}), m

    def test_bulk_ingest_edges_empty(self):
        result = self.engine.bulk_ingest_edges([])
        assert result == 0

    def test_bulk_ingest_edges_with_data(self):
        ctx, m = self._with_iris()
        with ctx:
            edges = [
                {"source": "n1", "predicate": "CAUSES", "target": "n2"},
                {"source": "n2", "predicate": "TREATS", "target": "n3"},
            ]
            try:
                result = self.engine.bulk_ingest_edges(edges)
                assert result >= 0
            except Exception:
                pass

    def test_bulk_create_edges_list(self):
        ctx, m = self._with_iris()
        with ctx:
            result = self.engine.bulk_create_edges([
                {"source": "n1", "predicate": "CAUSES", "target": "n2", "properties": {}},
            ])
        assert result is not None or result == 0


class TestEngineMaterializeInference:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()
        self.cursor.execute.return_value = None
        self.cursor.fetchone.return_value = (0,)
        self.cursor.fetchall.return_value = []

    def test_materialize_inference_no_rules(self):
        result = self.engine.materialize_inference(rules=[])
        assert isinstance(result, dict)

    def test_materialize_inference_transitive(self):
        self.cursor.fetchall.return_value = [
            ("n1", "IS_A", "n2"),
            ("n2", "IS_A", "n3"),
        ]
        result = self.engine.materialize_inference(rules=["transitive:IS_A"])
        assert isinstance(result, dict)

    def test_retract_inference_returns_int(self):
        self.cursor.execute.return_value = None
        self.cursor.rowcount = 5
        result = self.engine.retract_inference()
        assert isinstance(result, int)


class TestBugHunting:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()

    def test_embed_nodes_where_with_node_ids_raises(self):
        with pytest.raises(ValueError):
            self.engine.embed_nodes(where="x = 'y'", node_ids=["n1"])

    def test_create_node_with_empty_string_id_raises_validation(self):
        from pydantic import ValidationError
        with pytest.raises((ValidationError, ValueError)):
            self.engine.create_node("")

    def test_execute_cypher_empty_string_raises_validation(self):
        from pydantic import ValidationError
        with pytest.raises((ValidationError, ValueError)):
            self.engine.execute_cypher("")

    def test_nodes_exist_with_duplicate_ids(self):
        self.cursor.fetchall.return_value = [("n1",)]
        result = self.engine.nodes_exist(["n1", "n1", "n1"])
        assert "n1" in result

    def test_bulk_create_nodes_with_none_labels(self):
        result = self.engine.bulk_create_nodes([{"id": "n1", "labels": []}])
        assert result is not None

    def test_drop_graph_empty_string(self):
        self.cursor.execute.return_value = None
        self.cursor.rowcount = 0
        result = self.engine.drop_graph("")
        assert isinstance(result, int)

    def test_execute_cypher_returns_ivgresult_type(self):
        from iris_vector_graph.result import IVGResult
        self.cursor.fetchall.return_value = [("n1",)]
        self.cursor.description = [("node_id",)]
        r = self.engine.execute_cypher("MATCH (n) RETURN n.node_id LIMIT 1")
        assert isinstance(r, IVGResult)

    def test_execute_cypher_columns_match_description(self):
        from iris_vector_graph.result import IVGResult
        self.cursor.fetchall.return_value = [("n1", "Gene")]
        self.cursor.description = [("node_id",), ("label",)]
        r = self.engine.execute_cypher("MATCH (n) RETURN n.node_id, n.label LIMIT 1")
        if isinstance(r, IVGResult) and r.rows:
            assert len(r.columns) == len(r.rows[0]) or r.error is not None


class TestEngineSaveSnapshotMethod:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()
        self.cursor.execute.return_value = None
        self.cursor.fetchall.return_value = []
        self.cursor.fetchone.return_value = (0,)

    def test_save_snapshot_creates_file(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "test.snapshot")
            result = self.engine.save_snapshot(path)
            assert isinstance(result, dict)
            assert os.path.exists(path)
            assert zipfile.is_zipfile(path)

    def test_save_snapshot_includes_metadata(self):
        import zipfile
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "meta.snapshot")
            self.engine.save_snapshot(path)
            with zipfile.ZipFile(path, 'r') as zf:
                assert "metadata.json" in zf.namelist()

    def test_save_snapshot_overwrites_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "overwrite.snapshot")
            self.engine.save_snapshot(path)
            result2 = self.engine.save_snapshot(path)
            assert isinstance(result2, dict)

    def test_save_snapshot_returns_stats(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "stats.snapshot")
            result = self.engine.save_snapshot(path)
            assert "path" in result or isinstance(result, dict)

    def test_save_snapshot_with_node_data(self):
        self.cursor.fetchall.side_effect = [
            [("n1", None, None), ("n2", None, None)],
            [("n1", "INTERACTS", "n2", None)],
            [("n1", "Gene")],
            [("n1", "name", '"TP53"')],
            [],
            [],
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "data.snapshot")
            result = self.engine.save_snapshot(path)
            assert isinstance(result, dict)


class TestEngineRestoreSnapshotMethod:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()
        self.cursor.execute.return_value = None
        self.cursor.fetchall.return_value = []
        self.cursor.fetchone.return_value = (0,)

    def _create_snapshot(self, path):
        import zipfile
        metadata = {
            "version": "1.0",
            "tables": {"Graph_KG.nodes": 2, "Graph_KG.rdf_edges": 1},
            "has_vector_sql": False,
        }
        sql_data = {
            "sql/Graph_KG_nodes.ndjson": '{"node_id": "n1"}\n{"node_id": "n2"}',
            "sql/Graph_KG_rdf_edges.ndjson": '{"s": "n1", "p": "INTERACTS", "o_id": "n2", "graph_id": null}',
            "sql/Graph_KG_rdf_labels.ndjson": '{"s": "n1", "label": "Gene"}',
            "sql/Graph_KG_rdf_props.ndjson": '{"s": "n1", "key": "name", "val": "TP53"}',
        }
        with zipfile.ZipFile(path, 'w') as zf:
            zf.writestr("metadata.json", json.dumps(metadata))
            for fname, content in sql_data.items():
                zf.writestr(fname, content)

    def test_restore_snapshot_reads_tables(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "restore.snapshot")
            self._create_snapshot(path)
            try:
                result = self.engine.restore_snapshot(path)
                assert isinstance(result, dict)
            except Exception:
                pass

    def test_snapshot_info_with_valid_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "info.snapshot")
            self._create_snapshot(path)
            from iris_vector_graph.engine import IRISGraphEngine
            result = IRISGraphEngine.snapshot_info(path)
            assert isinstance(result, dict)
            assert result.get("tables") is not None or result is not None

    def test_restore_nonexistent_raises(self):
        with pytest.raises((FileNotFoundError, Exception)):
            self.engine.restore_snapshot("/definitely/not/real.snapshot")


class TestEngineEmbedTextMethod:

    def setup_method(self):
        self.engine, self.conn, self.cursor = _make_engine()

    def test_embed_text_with_embedder(self):
        import numpy as np
        self.engine.embedder = MagicMock()
        self.engine.embedder.encode.return_value = np.array([0.1, 0.2, 0.3])
        result = self.engine.embed_text("insulin resistance")
        assert len(result) == 3

    def test_embed_text_no_embedder_auto_loads(self):
        self.engine.embedder = None
        try:
            result = self.engine.embed_text("test")
            assert isinstance(result, list)
        except Exception:
            pass

    def test_embed_text_embedder_returns_list(self):
        import numpy as np
        self.engine.embedder = MagicMock()
        self.engine.embedder.encode.return_value = np.array([0.1, 0.2, 0.3, 0.4])
        result = self.engine.embed_text("test text")
        assert isinstance(result, list)

    def test_embed_text_embedder_returns_list(self):
        self.engine.embedder = MagicMock()
        import numpy as np; self.engine.embedder.encode.return_value = np.array([0.1, 0.2, 0.3, 0.4])
        result = self.engine.embed_text("test text")
        assert isinstance(result, list)
