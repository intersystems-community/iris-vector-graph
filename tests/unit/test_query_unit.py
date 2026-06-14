"""
Unit tests for _engine/query.py covering:
- execute_cypher: CALL DB.LABELS()...UNION pattern
- execute_cypher: RETURN DISTINCT...UNION ALL...ENTITY pattern
- execute_cypher: MATCH ()...COUNT(*)...UNION ALL pattern
- execute_cypher: semicolon multi-part with CALL
- execute_cypher: EXPLAIN prefix
- execute_cypher: CREATE CONSTRAINT / DROP INDEX / etc (DDL ops)
- execute_cypher: subsequent_queries chain
- execute_cypher: read_only mutation rejection
- execute_cypher: approx_count_distinct path
- _execute_parsed: is_transactional, native_sql=False paths
- _extract_traversal: various parse outcomes
- _execute_var_length_cypher: count_match path, source_id=None path
- _execute_shortest_path_cypher: return_path_funcs (length/nodes/relationships)
- _route_var_length: temporal_window, count_match, return_properties enrichment
- execute_aql: delegates to execute_cypher
"""
import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


def _make_eng(dim=4):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.execute.return_value = None
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.description = []
    cursor.close.return_value = None
    return IRISGraphEngine(conn, embedding_dimension=dim), conn, cursor


def _make_store(**caps):
    store = MagicMock()
    store.capabilities.return_value = caps
    return store


# ---------------------------------------------------------------------------
# execute_cypher special-case patterns
# ---------------------------------------------------------------------------

class TestExecuteCypherSpecialCases:

    def test_call_db_labels_yield_union(self):
        """CALL DB.LABELS() YIELD ... UNION pattern returns schema overview."""
        eng, conn, cursor = _make_eng()
        cursor.fetchall.return_value = [("name",)]  # prop keys
        with patch.object(eng, "_try_system_procedure") as mock_proc:
            mock_proc.side_effect = lambda p: (
                IVGResult(columns=["label"], rows=[["Gene"], ["Disease"]])
                if "labels" in p.procedure_name
                else IVGResult(columns=["relationshipType"], rows=[["TREATS"]])
            )
            result = eng.execute_cypher(
                "CALL DB.LABELS() YIELD label RETURN label UNION CALL DB.RELATIONSHIPTYPES() YIELD relationshipType RETURN relationshipType"
            )
        assert isinstance(result, IVGResult)
        assert result.columns == ["result"]
        assert len(result.rows) == 3  # labels, rels, propKeys

    def test_return_distinct_union_all_entity(self):
        """RETURN DISTINCT...UNION ALL...ENTITY pattern returns entity browser rows."""
        eng, conn, cursor = _make_eng()
        call_seq = iter([
            [("n1",), ("n2",)],  # nodes
            [("TREATS",)],       # rels
        ])
        cursor.fetchall.side_effect = lambda: next(call_seq)
        result = eng.execute_cypher(
            "MATCH (n) RETURN DISTINCT 'node' as entity, n.id as id UNION ALL MATCH ()-[r]->() RETURN DISTINCT 'relationship' as entity, type(r) as id"
        )
        assert isinstance(result, IVGResult)
        assert result.columns == ["entity", "id"]

    def test_match_count_union_all(self):
        """MATCH ()...COUNT(*)...UNION ALL pattern returns node/edge counts."""
        eng, conn, cursor = _make_eng()
        call_seq = iter([(42,), (17,)])
        cursor.fetchone.side_effect = lambda: next(call_seq)
        result = eng.execute_cypher(
            "MATCH () RETURN COUNT(*) AS nodes UNION ALL MATCH ()-[r]->() RETURN COUNT(*) AS relationships"
        )
        assert isinstance(result, IVGResult)
        assert result.columns == ["result"]
        assert len(result.rows) == 2

    def test_explain_prefix(self):
        """EXPLAIN returns a plan stub."""
        eng, conn, cursor = _make_eng()
        result = eng.execute_cypher("EXPLAIN MATCH (n) RETURN n")
        assert isinstance(result, IVGResult)
        assert result.columns == ["Plan"]
        assert "IRIS" in result.rows[0][0]

    def test_create_constraint_returns_empty(self):
        """CREATE CONSTRAINT is a no-op returning empty result."""
        eng, conn, cursor = _make_eng()
        result = eng.execute_cypher("CREATE CONSTRAINT ON (n:Node) ASSERT n.id IS UNIQUE")
        assert isinstance(result, IVGResult)
        assert result.rows == []

    def test_drop_index_returns_empty(self):
        eng, conn, cursor = _make_eng()
        result = eng.execute_cypher("DROP INDEX node_idx")
        assert isinstance(result, IVGResult)
        assert result.rows == []

    def test_create_fulltext_returns_empty(self):
        eng, conn, cursor = _make_eng()
        result = eng.execute_cypher("CREATE FULLTEXT INDEX ft_idx FOR (n:Node) ON EACH [n.name]")
        assert isinstance(result, IVGResult)
        assert result.rows == []

    def test_semicolon_multi_part_with_call(self):
        """Semicolon-separated multi-part query with CALL is split and executed."""
        eng, conn, cursor = _make_eng()
        # Patch execute_cypher recursion — each sub-call returns a simple result
        results = iter([
            IVGResult(columns=["label"], rows=[["Gene"]]),
            IVGResult(columns=["label"], rows=[["Disease"]]),
        ])

        def _side(q, parameters=None, read_only=False):
            # Only intercept the sub-parts; prevent infinite recursion
            if ";" not in q:
                return next(results)
            return eng.__class__.execute_cypher(eng, q, parameters, read_only)

        with patch.object(eng, "execute_cypher", side_effect=_side):
            # Call the real method with `;` in the query
            result = eng.__class__.execute_cypher(
                eng,
                "CALL db.labels() YIELD label RETURN label; CALL db.labels() YIELD label RETURN label",
                parameters=None,
            )
        assert isinstance(result, IVGResult)

    def test_subsequent_queries_chain_passes_params(self):
        """subsequent_queries list is executed in order with accumulated params."""
        eng, conn, cursor = _make_eng()

        # Build a minimal parsed mock with subsequent_queries
        part2 = MagicMock()
        part2.subsequent_queries = []
        part2.procedure_call = None
        part2.var_length_paths = []
        part2.is_mutation = False

        part1 = MagicMock()
        part1.subsequent_queries = [part2]
        part1.procedure_call = None
        part1.var_length_paths = []
        part1.is_mutation = False

        res1 = IVGResult(columns=["x"], rows=[[42]])
        res2 = IVGResult(columns=["y"], rows=[[99]])

        with patch("iris_vector_graph._engine.query.parse_query", return_value=part1):
            with patch.object(eng, "_execute_parsed", side_effect=[res1, res2]):
                result = eng.execute_cypher("MATCH (n) WITH n MATCH (n)-[r]->(m) RETURN m")
        # Last result is returned
        assert result is res2

    def test_read_only_blocks_mutation(self):
        """read_only=True raises PermissionError on mutation queries."""
        eng, conn, cursor = _make_eng()

        parsed = MagicMock()
        parsed.subsequent_queries = []
        parsed.procedure_call = None
        parsed.var_length_paths = []
        parsed.is_mutation = True

        with patch("iris_vector_graph._engine.query.parse_query", return_value=parsed):
            with pytest.raises(PermissionError, match="Read-only mode"):
                eng.execute_cypher("CREATE (n:Node {id: 'x'})", read_only=True)

    def test_approx_count_distinct_dispatched(self):
        """approx_count_distinct pattern is dispatched to dedicated method."""
        eng, conn, cursor = _make_eng()
        with patch.object(eng, "_execute_approx_count_distinct",
                          return_value=IVGResult(columns=["approx"], rows=[[1000]])) as mock_acd:
            result = eng.execute_cypher("MATCH (n) RETURN approx_count_distinct(n) AS est")
        mock_acd.assert_called_once()
        assert result.columns == ["approx"]


# ---------------------------------------------------------------------------
# _execute_parsed paths
# ---------------------------------------------------------------------------

class TestExecuteParsed:

    def test_is_transactional_path(self):
        """is_transactional=True routes to store.execute_transaction."""
        eng, conn, cursor = _make_eng()
        store = MagicMock()
        store.execute_transaction.return_value = IVGResult(columns=[], rows=[])
        eng._store = store

        sql_query = MagicMock()
        sql_query.var_length_paths = []
        sql_query.is_transactional = True
        sql_query.sql = "INSERT INTO ..."
        sql_query.parameters = [["p1"]]
        sql_query.query_metadata = {}

        parsed = MagicMock()
        parsed.subsequent_queries = []
        parsed.procedure_call = None
        parsed.is_mutation = False

        with patch("iris_vector_graph._engine.query.translate_to_sql", return_value=sql_query):
            result = eng._execute_parsed(parsed, {})
        store.execute_transaction.assert_called_once()

    def test_native_sql_false_falls_through_to_query_nodes(self):
        """native_sql=False falls through _extract_traversal→query_nodes."""
        eng, conn, cursor = _make_eng()
        eng._store_capabilities = {"native_sql": False}
        store = MagicMock()
        store.query_nodes.return_value = IVGResult(columns=["id"], rows=[["n1"]])
        eng._store = store

        sql_query = MagicMock()
        sql_query.var_length_paths = []
        sql_query.is_transactional = False
        sql_query.sql = "SELECT id FROM ..."
        sql_query.parameters = []
        sql_query.query_metadata = {}

        parsed = MagicMock()
        parsed.subsequent_queries = []
        parsed.procedure_call = None
        parsed.is_mutation = False
        parsed.query_parts = []
        parsed.return_clause = None
        parsed.limit = None

        with patch("iris_vector_graph._engine.query.translate_to_sql", return_value=sql_query):
            with patch.object(eng, "_extract_traversal", return_value=None):
                result = eng._execute_parsed(parsed, {})
        store.query_nodes.assert_called_once()

    def test_native_sql_false_traversal_executed(self):
        """native_sql=False with traversal found routes to _execute_traversal."""
        eng, conn, cursor = _make_eng()
        eng._store_capabilities = {"native_sql": False}
        store = MagicMock()
        store.execute_bfs.return_value = IVGResult(columns=["id", "hops"], rows=[["n2", 1]])
        eng._store = store

        traversal = {
            "source_id": "n1",
            "predicates": ["TREATS"],
            "direction": "out",
            "is_count": False,
            "return_col": "id",
        }

        sql_query = MagicMock()
        sql_query.var_length_paths = []
        sql_query.is_transactional = False
        sql_query.sql = "..."
        sql_query.parameters = []
        sql_query.query_metadata = {}

        parsed = MagicMock()
        parsed.subsequent_queries = []
        parsed.procedure_call = None
        parsed.is_mutation = False

        with patch("iris_vector_graph._engine.query.translate_to_sql", return_value=sql_query):
            with patch.object(eng, "_extract_traversal", return_value=traversal):
                result = eng._execute_parsed(parsed, {})
        store.execute_bfs.assert_called_once()


# ---------------------------------------------------------------------------
# _extract_traversal
# ---------------------------------------------------------------------------

class TestExtractTraversal:

    def _make_parsed_traversal(self, src_id="node1", direction_both=False, with_count=False):
        """Build a minimal parsed AST mock for _extract_traversal."""
        from iris_vector_graph.cypher.ast import Direction

        node = MagicMock()
        node.labels = []
        node.properties = {"id": src_id}

        dst_node = MagicMock()
        dst_node.labels = []
        dst_node.properties = {}

        rel = MagicMock()
        rel.types = ["TREATS"]
        rel.variable_length = None
        rel.direction = Direction.BOTH if direction_both else Direction.OUTGOING

        pattern = MagicMock()
        pattern.nodes = [node, dst_node]
        pattern.relationships = [rel]

        clause = MagicMock()
        clause.patterns = [pattern]

        query_part = MagicMock()
        query_part.clauses = [clause]

        ret_item = MagicMock()
        ret_item.alias = "target"
        if with_count:
            ret_item.expression.function_name = "COUNT"
        else:
            del ret_item.expression.function_name
            ret_item.expression.property_name = "id"

        return_clause = MagicMock()
        return_clause.items = [ret_item]

        parsed = MagicMock()
        parsed.query_parts = [query_part]
        parsed.return_clause = return_clause
        parsed.limit = None
        return parsed

    def test_extracts_traversal_out(self):
        eng, conn, cursor = _make_eng()
        parsed = self._make_parsed_traversal("n1")
        result = eng._extract_traversal(parsed, {})
        assert result is not None
        assert result["source_id"] == "n1"
        assert result["direction"] == "out"

    def test_extracts_traversal_both(self):
        eng, conn, cursor = _make_eng()
        parsed = self._make_parsed_traversal("n1", direction_both=True)
        result = eng._extract_traversal(parsed, {})
        assert result is not None
        assert result["direction"] == "both"

    def test_no_patterns_returns_none(self):
        eng, conn, cursor = _make_eng()
        clause = MagicMock()
        clause.patterns = []
        qp = MagicMock()
        qp.clauses = [clause]
        parsed = MagicMock()
        parsed.query_parts = [qp]
        result = eng._extract_traversal(parsed, {})
        assert result is None

    def test_no_src_id_returns_none(self):
        eng, conn, cursor = _make_eng()
        node = MagicMock()
        node.properties = {}  # no 'id' key
        dst = MagicMock()
        rel = MagicMock()
        rel.variable_length = None
        pat = MagicMock()
        pat.nodes = [node, dst]
        pat.relationships = [rel]
        clause = MagicMock()
        clause.patterns = [pat]
        qp = MagicMock()
        qp.clauses = [clause]
        parsed = MagicMock()
        parsed.query_parts = [qp]
        result = eng._extract_traversal(parsed, {})
        assert result is None

    def test_variable_length_rel_returns_none(self):
        eng, conn, cursor = _make_eng()
        node = MagicMock()
        node.properties = {"id": "n1"}
        dst = MagicMock()
        rel = MagicMock()
        rel.variable_length = (1, 3)  # variable-length → skip traversal
        pat = MagicMock()
        pat.nodes = [node, dst]
        pat.relationships = [rel]
        clause = MagicMock()
        clause.patterns = [pat]
        qp = MagicMock()
        qp.clauses = [clause]
        parsed = MagicMock()
        parsed.query_parts = [qp]
        result = eng._extract_traversal(parsed, {})
        assert result is None

    def test_param_reference_resolved(self):
        eng, conn, cursor = _make_eng()
        parsed = self._make_parsed_traversal("$nodeId")
        result = eng._extract_traversal(parsed, {"nodeId": "resolved_node"})
        assert result is not None
        assert result["source_id"] == "resolved_node"

    def test_is_count_true(self):
        eng, conn, cursor = _make_eng()
        parsed = self._make_parsed_traversal("n1", with_count=True)
        result = eng._extract_traversal(parsed, {})
        assert result is not None
        assert result["is_count"] is True

    def test_exception_returns_none(self):
        eng, conn, cursor = _make_eng()
        parsed = MagicMock()
        parsed.query_parts = []  # will raise IndexError inside
        result = eng._extract_traversal(parsed, {})
        assert result is None


# ---------------------------------------------------------------------------
# _route_var_length edge cases
# ---------------------------------------------------------------------------

class TestRouteVarLength:

    def _make_sql_query(self, weighted=False, shortest=False, all_shortest=False,
                        min_hops=1, props=None, temporal=False, ts_start=0, ts_end=9999,
                        src="n1", src_var=None, direction="out", types=None, max_hops=5):
        vl = {
            "weighted": weighted,
            "shortest": shortest,
            "all_shortest": all_shortest,
            "min_hops": min_hops,
            "properties": props or {},
            "temporal_window": temporal,
            "ts_start": ts_start,
            "ts_end": ts_end,
            "source_var": src_var,
            "direction": direction,
            "types": types or [],
            "max_hops": max_hops,
        }
        sq = MagicMock()
        sq.var_length_paths = [vl]
        sq.sql = f"SELECT id FROM Graph_KG.nodes LIMIT 10"
        sq.parameters = [[src, "Graph_KG.nodes"]]
        sq.query_metadata = {}
        return sq

    def test_temporal_window_dispatched(self):
        eng, conn, cursor = _make_eng()
        store = MagicMock()
        store.execute_temporal_cypher.return_value = IVGResult(columns=["id"], rows=[["n2"]])
        eng._store = store
        sq = self._make_sql_query(temporal=True, ts_start=100, ts_end=200)
        eng._nkg_dirty = False
        result = eng._route_var_length(sq, {})
        store.execute_temporal_cypher.assert_called_once()

    def test_count_match_path(self):
        """When SQL contains SELECT COUNT(DISTINCT ...) AS col, BFS count is returned."""
        eng, conn, cursor = _make_eng()
        store = MagicMock()
        store.execute_bfs.return_value = IVGResult(columns=["id", "hops"],
                                                   rows=[["n2", 1], ["n3", 2]])
        eng._store = store
        vl = {
            "weighted": False, "shortest": False, "all_shortest": False,
            "min_hops": 1, "properties": {}, "temporal_window": False,
            "direction": "out", "types": [], "max_hops": 3,
        }
        sq = MagicMock()
        sq.var_length_paths = [vl]
        sq.sql = "SELECT COUNT(DISTINCT n.id) AS neighbor_count FROM Graph_KG.nodes"
        sq.parameters = [["n1"]]
        sq.query_metadata = {}
        eng._nkg_dirty = False
        result = eng._route_var_length(sq, {})
        assert result.columns == ["neighbor_count"]
        assert result.rows[0][0] == 2

    def test_return_properties_enrichment(self):
        """return_properties present → enriches BFS results with node properties."""
        eng, conn, cursor = _make_eng()
        bfs_result = IVGResult(columns=["id", "hops"], rows=[["n2", 1]])
        props_result = IVGResult(columns=["id", "label", "name"],
                                 rows=[["n2", "Gene", "BRCA1"]])
        store = MagicMock()
        store.execute_bfs.return_value = bfs_result
        store.get_nodes.return_value = props_result
        eng._store = store

        metadata = MagicMock()
        metadata.return_properties = ["name"]

        vl = {
            "weighted": False, "shortest": False, "all_shortest": False,
            "min_hops": 1, "properties": {}, "temporal_window": False,
            "direction": "out", "types": [], "max_hops": 3,
        }
        sq = MagicMock()
        sq.var_length_paths = [vl]
        sq.sql = "SELECT id FROM Graph_KG.nodes LIMIT 10"
        sq.parameters = [["n1"]]
        sq.query_metadata = metadata

        eng._nkg_dirty = False
        result = eng._route_var_length(sq, {})
        assert "name" in result.columns
        store.get_nodes.assert_called_once()

    def test_nkg_dirty_raises(self):
        eng, conn, cursor = _make_eng()
        eng._nkg_dirty = True
        from iris_vector_graph.errors import IndexNotSyncedError
        vl = {
            "weighted": False, "shortest": False, "all_shortest": False,
            "min_hops": 1, "properties": {}, "temporal_window": False,
            "direction": "out", "types": [], "max_hops": 3,
        }
        sq = MagicMock()
        sq.var_length_paths = [vl]
        sq.sql = ""
        sq.parameters = []
        sq.query_metadata = {}
        with pytest.raises(IndexNotSyncedError):
            eng._route_var_length(sq, {})


# ---------------------------------------------------------------------------
# _execute_shortest_path_cypher return_path_funcs
# ---------------------------------------------------------------------------

class TestExecuteShortestPathCypher:

    def _make_sql_query(self, src="n1", dst="n2", types=None, max_hops=5,
                        direction="both", all_shortest=False,
                        return_path_funcs=None):
        vl = {
            "weighted": False,
            "shortest": True,
            "all_shortest": all_shortest,
            "min_hops": 1,
            "properties": {},
            "src_id_param": src,
            "dst_id_param": dst,
            "types": types or [],
            "max_hops": max_hops,
            "direction": direction,
            "return_path_funcs": return_path_funcs or [],
            "source_var": None,
            "target_var": None,
        }
        sq = MagicMock()
        sq.var_length_paths = [vl]
        sq.sql = "SELECT ..."
        sq.parameters = []
        sq.query_metadata = {}
        return sq

    def test_return_path_funcs_length(self):
        eng, conn, cursor = _make_eng()
        path_json = json.dumps({"nodes": ["n1", "n2"], "rels": [{"type": "TREATS"}]})
        store = MagicMock()
        store.execute_shortest_path.return_value = IVGResult(
            columns=["path", "length"], rows=[[path_json, 1]]
        )
        eng._store = store
        eng._nkg_dirty = False

        sq = self._make_sql_query(return_path_funcs=["length"])
        result = eng._execute_shortest_path_cypher(sq, {})
        assert "length" in result.columns
        assert result.rows[0][0] == 1

    def test_return_path_funcs_nodes(self):
        eng, conn, cursor = _make_eng()
        path_json = json.dumps({"nodes": ["n1", "n2"], "rels": []})
        store = MagicMock()
        store.execute_shortest_path.return_value = IVGResult(
            columns=["path", "length"], rows=[[path_json, 1]]
        )
        eng._store = store
        eng._nkg_dirty = False

        sq = self._make_sql_query(return_path_funcs=["nodes"])
        result = eng._execute_shortest_path_cypher(sq, {})
        assert "nodes" in result.columns
        assert result.rows[0][0] == ["n1", "n2"]

    def test_return_path_funcs_relationships(self):
        eng, conn, cursor = _make_eng()
        path_json = json.dumps({"nodes": ["n1"], "rels": [{"type": "TREATS"}]})
        store = MagicMock()
        store.execute_shortest_path.return_value = IVGResult(
            columns=["path", "length"], rows=[[path_json, 1]]
        )
        eng._store = store
        eng._nkg_dirty = False

        sq = self._make_sql_query(return_path_funcs=["relationships"])
        result = eng._execute_shortest_path_cypher(sq, {})
        assert "relationships" in result.columns

    def test_return_path_means_pass_through(self):
        """return_path_funcs=['path'] → return result directly."""
        eng, conn, cursor = _make_eng()
        raw = IVGResult(columns=["path", "length"], rows=[["...", 2]])
        store = MagicMock()
        store.execute_shortest_path.return_value = raw
        eng._store = store
        eng._nkg_dirty = False

        sq = self._make_sql_query(return_path_funcs=["path"])
        result = eng._execute_shortest_path_cypher(sq, {})
        assert result is raw

    def test_source_none_raises(self):
        eng, conn, cursor = _make_eng()
        eng._nkg_dirty = False
        vl = {
            "weighted": False, "shortest": True, "all_shortest": False,
            "min_hops": 1, "properties": {},
            "src_id_param": None, "dst_id_param": None,
            "types": [], "max_hops": 5, "direction": "both",
            "return_path_funcs": [], "source_var": None, "target_var": None,
        }
        sq = MagicMock()
        sq.var_length_paths = [vl]
        sq.sql = ""
        sq.parameters = []
        sq.query_metadata = {}
        with pytest.raises(ValueError, match="shortestPath requires"):
            eng._execute_shortest_path_cypher(sq, {})


# ---------------------------------------------------------------------------
# _execute_var_length_cypher paths
# ---------------------------------------------------------------------------

class TestExecuteVarLengthCypher:

    def _make_var_length_sql(self, src="n1", src_var=None, direction="out",
                              types=None, max_hops=3, min_hops=1,
                              sql_str="SELECT id FROM ...",
                              count_sql=None):
        vl = {
            "types": types or [],
            "max_hops": max_hops,
            "min_hops": min_hops,
            "properties": {},
            "direction": direction,
            "source_var": src_var,
        }
        sq = MagicMock()
        sq.var_length_paths = [vl]
        sq.sql = count_sql or sql_str
        sq.parameters = [[src]]
        sq.query_metadata = {}
        return sq

    def test_source_id_none_returns_empty(self):
        eng, conn, cursor = _make_eng()
        eng._nkg_dirty = False
        vl = {
            "types": [], "max_hops": 3, "min_hops": 1,
            "properties": {}, "direction": "out", "source_var": None,
        }
        sq = MagicMock()
        sq.var_length_paths = [vl]
        sq.sql = ""
        sq.parameters = [[]]  # no string params → source_id=None
        sq.query_metadata = {}
        with patch.object(eng, "_detect_arno", return_value=False):
            with patch("iris_vector_graph.schema._call_classmethod",
                       side_effect=Exception("boom")):
                result = eng._execute_var_length_cypher(sq, {})
        assert isinstance(result, IVGResult)
        assert result.rows == []

    def test_count_match_uses_bfs_count(self):
        eng, conn, cursor = _make_eng()
        eng._nkg_dirty = False
        count_sql = "SELECT COUNT(DISTINCT n.id) AS reach FROM Graph_KG.nodes LIMIT 100"
        sq = self._make_var_length_sql(count_sql=count_sql)
        with patch.object(eng, "_detect_arno", return_value=False):
            with patch("iris_vector_graph.schema._call_classmethod",
                       return_value="42"):
                result = eng._execute_var_length_cypher(sq, {})
        assert result.columns == ["reach"]
        assert result.rows[0][0] == 42

    def test_fallback_bfs_json_sorted(self):
        eng, conn, cursor = _make_eng()
        eng._nkg_dirty = False
        nodes = [{"node_id": "n2", "hops": 1}, {"node_id": "n3", "hops": 2}]
        sq = self._make_var_length_sql()
        with patch.object(eng, "_detect_arno", return_value=False):
            with patch("iris_vector_graph.schema._call_classmethod",
                       return_value=json.dumps(nodes)):
                result = eng._execute_var_length_cypher(sq, {})
        assert isinstance(result, IVGResult)


# ---------------------------------------------------------------------------
# execute_aql delegation
# ---------------------------------------------------------------------------

class TestExecuteAql:

    def test_delegates_to_execute_cypher(self):
        eng, conn, cursor = _make_eng()
        with patch.object(eng, "execute_cypher",
                          return_value=IVGResult(columns=["id"], rows=[["n1"]])) as mock_ec:
            result = eng.execute_aql(
                "FOR v IN 1..2 OUTBOUND @start g RETURN v._key",
                bind_vars={"start": "n1"},
            )
        mock_ec.assert_called_once()
        assert result.columns == ["id"]
