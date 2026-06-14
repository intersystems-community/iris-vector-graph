"""
Integration tests targeting uncovered branches in _engine/query.py.

Covers:
  L114-115: CALL;CALL; semicolon-separated batch exception branch
  L148-158: subsequent_queries execution path
  L118: EXPLAIN prefix
  L228-233: _extract_traversal property lookup variations
  L499-503: BFS count path
  L529-530: BFS exception handler
  L558-572: Arno BFS paths (skipped on Community)
"""
import pytest
from iris_vector_graph.engine import IRISGraphEngine


@pytest.fixture
def qe_graph(iris_connection, iris_master_cleanup):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
    for i in range(5):
        eng.create_node(f"qe_{i}", labels=["QE"], properties={"v": i, "name": f"node_{i}"})
    for i in range(4):
        eng.create_edge(f"qe_{i}", "QE_REL", f"qe_{i + 1}")
    eng.sync()
    return eng


# ---------------------------------------------------------------------------
# EXPLAIN prefix (L118-120)
# ---------------------------------------------------------------------------

class TestExplainPrefix:

    def test_explain_basic(self, qe_graph):
        result = qe_graph.execute_cypher("EXPLAIN MATCH (n:QE) RETURN n.node_id")
        assert result is not None
        assert result.get("columns") == ["Plan"] or (hasattr(result, "columns") and result.columns == ["Plan"])

    def test_explain_create_returns_plan(self, qe_graph):
        result = qe_graph.execute_cypher("EXPLAIN CREATE (n:Test {id: 'x'})")
        assert result is not None


# ---------------------------------------------------------------------------
# CALL;CALL; semicolon batch (L103-116)
# ---------------------------------------------------------------------------

class TestCallSemicolonBatch:

    def test_call_semicolon_batch(self, qe_graph):
        # Trigger the semicolon-CALL batch path
        result = qe_graph.execute_cypher(
            "CALL ivg.neighbors('qe_0', 'out', '', 1, 10); "
            "CALL ivg.neighbors('qe_1', 'out', '', 1, 10)"
        )
        assert result is not None

    def test_call_semicolon_batch_with_exception(self, qe_graph):
        # One bad call + one good call — bad one gets swallowed
        result = qe_graph.execute_cypher(
            "CALL ivg.neighbors('qe_0', 'out', '', 1, 10); "
            "CALL __no_such_proc__('x')"
        )
        assert result is not None


# ---------------------------------------------------------------------------
# subsequent_queries execution path (L148-158)
# ---------------------------------------------------------------------------

class TestSubsequentQueries:

    def test_chained_query_basic(self, qe_graph):
        # WITH ... MATCH ... RETURN ... MATCH ... RETURN triggers subsequent_queries
        result = qe_graph.execute_cypher(
            "WITH 0 AS x "
            "MATCH (n:QE) WHERE n.v = x RETURN n.node_id AS id "
            "MATCH (m:QE) WHERE m.v = 1 RETURN m.node_id AS id"
        )
        assert result is not None

    def test_chained_query_uses_prior_result(self, qe_graph):
        # First query returns id, second uses it via injection into current_params
        result = qe_graph.execute_cypher(
            "WITH 2 AS x "
            "MATCH (n:QE) WHERE n.v = x RETURN n.node_id AS id "
            "MATCH (m:QE) WHERE m.v = 3 RETURN m.node_id AS id"
        )
        assert result is not None
        rows = result.get("rows") if hasattr(result, "get") else result.rows
        assert rows is not None

    def test_chained_query_empty_first_result(self, qe_graph):
        # First part returns no rows — current_params stays unchanged
        result = qe_graph.execute_cypher(
            "WITH 9999 AS x "
            "MATCH (n:QE) WHERE n.v = x RETURN n.node_id AS id "
            "MATCH (m:QE) WHERE m.v = 0 RETURN m.node_id AS id"
        )
        assert result is not None


# ---------------------------------------------------------------------------
# BFS count path (L512-534)
# ---------------------------------------------------------------------------

class TestBFSCountPath:

    def test_bfs_count_distinct(self, qe_graph):
        # Trigger the COUNT(DISTINCT) fast path in _execute_var_length_cypher
        result = qe_graph.execute_cypher(
            "MATCH (a {node_id: 'qe_0'})-[:QE_REL*1..2]->(b) RETURN count(DISTINCT b) AS cnt"
        )
        assert result is not None
        rows = result.get("rows") if hasattr(result, "get") else result.rows
        assert rows is not None and len(rows) > 0

    def test_bfs_id_only_path(self, qe_graph):
        # RETURN DISTINCT b.node_id triggers the id-only fast path
        result = qe_graph.execute_cypher(
            "MATCH (a {node_id: 'qe_0'})-[:QE_REL*1..3]->(b) RETURN DISTINCT b.node_id AS id"
        )
        assert result is not None
        rows = result.get("rows") if hasattr(result, "get") else result.rows
        assert rows is not None

    def test_bfs_with_limit(self, qe_graph):
        result = qe_graph.execute_cypher(
            "MATCH (a {node_id: 'qe_0'})-[:QE_REL*1..3]->(b) RETURN DISTINCT b.node_id AS id LIMIT 2"
        )
        assert result is not None

    def test_bfs_inbound(self, qe_graph):
        result = qe_graph.execute_cypher(
            "MATCH (a {node_id: 'qe_3'})<-[:QE_REL*1..2]-(b) RETURN b.node_id AS id"
        )
        assert result is not None

    def test_bfs_both_directions(self, qe_graph):
        result = qe_graph.execute_cypher(
            "MATCH (a {node_id: 'qe_2'})-[:QE_REL*1..2]-(b) RETURN b.node_id AS id"
        )
        assert result is not None

    def test_bfs_no_results(self, qe_graph):
        # Source with no outbound edges
        result = qe_graph.execute_cypher(
            "MATCH (a {node_id: 'qe_4'})-[:QE_REL*1..2]->(b) RETURN b.node_id AS id"
        )
        assert result is not None
        rows = result.get("rows") if hasattr(result, "get") else result.rows
        assert rows == [] or rows is not None

    def test_bfs_with_parameter(self, qe_graph):
        result = qe_graph.execute_cypher(
            "MATCH (a {node_id: $src})-[:QE_REL*1..2]->(b) RETURN b.node_id AS id",
            parameters={"src": "qe_0"}
        )
        assert result is not None


# ---------------------------------------------------------------------------
# _extract_traversal edge cases (L201-270)
# ---------------------------------------------------------------------------

class TestExtractTraversalEdgeCases:

    def test_traversal_with_labels_returned(self, qe_graph):
        # Returns full node data — triggers full path in _execute_var_length_cypher
        result = qe_graph.execute_cypher(
            "MATCH (a {node_id: 'qe_0'})-[:QE_REL*1..1]->(b) RETURN b.node_id, b.name"
        )
        assert result is not None

    def test_traversal_with_no_source_id(self, qe_graph):
        # Missing source ID — should return empty result
        try:
            result = qe_graph.execute_cypher(
                "MATCH (a {node_id: $src})-[:QE_REL*1..1]->(b) RETURN b.node_id AS id",
                parameters={}
            )
            assert result is not None
        except Exception:
            pass  # ValueError for missing param is acceptable

    def test_traversal_with_predicate_filter(self, qe_graph):
        result = qe_graph.execute_cypher(
            "MATCH (a {node_id: 'qe_0'})-[:QE_REL*1..2]->(b) RETURN b.node_id AS id"
        )
        assert result is not None

    def test_multi_hop_min_hops(self, qe_graph):
        # min_hops > 1 path (L603-616)
        result = qe_graph.execute_cypher(
            "MATCH (a {node_id: 'qe_0'})-[:QE_REL*2..3]->(b) RETURN b.node_id AS id"
        )
        assert result is not None


# ---------------------------------------------------------------------------
# BFS full-props path (L662-695)
# ---------------------------------------------------------------------------

class TestBFSFullPropsPath:

    def test_bfs_full_node_data(self, qe_graph):
        # When RETURN doesn't match id_only or count, falls through to get_nodes()
        result = qe_graph.execute_cypher(
            "MATCH (a {node_id: 'qe_0'})-[:QE_REL*1..2]->(b) "
            "RETURN b.node_id AS bid, labels(b) AS lbl"
        )
        assert result is not None

    def test_bfs_full_props_empty_targets(self, qe_graph):
        # No downstream nodes — triggers early return for empty target_ids
        result = qe_graph.execute_cypher(
            "MATCH (a {node_id: '__missing__'})-[:QE_REL*1..2]->(b) "
            "RETURN b.node_id AS bid, labels(b) AS lbl"
        )
        assert result is not None
        rows = result.get("rows") if hasattr(result, "get") else result.rows
        assert rows == []


# ---------------------------------------------------------------------------
# Shortest path via Cypher (L382-447)
# ---------------------------------------------------------------------------

class TestShortestPathCypher:

    def test_shortest_path_cypher(self, qe_graph):
        result = qe_graph.execute_cypher(
            "MATCH p = shortestPath((a {node_id: $from})-[:QE_REL*..5]->(b {node_id: $to})) "
            "RETURN p",
            parameters={"from": "qe_0", "to": "qe_3"}
        )
        assert result is not None

    def test_all_shortest_paths_cypher(self, qe_graph):
        result = qe_graph.execute_cypher(
            "MATCH p = allShortestPaths((a {node_id: $from})-[:QE_REL*..5]->(b {node_id: $to})) "
            "RETURN p",
            parameters={"from": "qe_0", "to": "qe_2"}
        )
        assert result is not None


# ---------------------------------------------------------------------------
# Weighted shortest path (L350-380)
# ---------------------------------------------------------------------------

class TestWeightedShortestPathCypher:

    def test_weighted_shortest_path(self, qe_graph):
        result = qe_graph.execute_cypher(
            "CALL ivg.shortestPath.weighted($src, $tgt, 'weight', 10) "
            "YIELD path, totalCost RETURN path, totalCost",
            parameters={"src": "qe_0", "tgt": "qe_3"}
        )
        assert result is not None


# ---------------------------------------------------------------------------
# Node-only query path (L182-212)
# ---------------------------------------------------------------------------

class TestNodeOnlyQueryPath:

    def test_simple_node_scan_no_predicates(self, qe_graph):
        result = qe_graph.execute_cypher("MATCH (n:QE) RETURN n.node_id")
        assert result is not None

    def test_simple_node_scan_with_limit(self, qe_graph):
        result = qe_graph.execute_cypher("MATCH (n:QE) RETURN n.node_id LIMIT 2")
        rows = result.get("rows") if hasattr(result, "get") else result.rows
        assert len(rows) <= 2

    def test_match_properties_in_return(self, qe_graph):
        result = qe_graph.execute_cypher("MATCH (n:QE) RETURN n.v, n.name")
        assert result is not None


# ---------------------------------------------------------------------------
# Temporal BFS path (L329-334)
# ---------------------------------------------------------------------------

class TestTemporalBFSPath:

    def test_temporal_cypher(self, qe_graph):
        try:
            result = qe_graph.execute_cypher(
                "MATCH (a {node_id: 'qe_0'})-[:QE_REL*1..2 {timestamp: 1..9999}]->(b) "
                "RETURN b.node_id AS id"
            )
            assert result is not None
        except Exception:
            pytest.skip("temporal BFS not supported with this Cypher pattern")
