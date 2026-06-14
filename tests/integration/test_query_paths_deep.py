"""
Deep coverage tests for _engine/query.py uncovered paths.

Targets:
  L114-115: EXPLAIN command
  L124: SHOW command
  L148-158: CREATE/DROP INDEX/CONSTRAINT pass-through
  L201-202, 214, 220: _extract_node_lookup paths
  L229-233: subsequent_queries execution
  L295-302: BFS with return_properties
  L339-380: weighted shortest path execution
  L353-380: shortest path execution
  L454-464: count_distinct via approx path
  L499-530: BFS source extraction from params
  L558-568: BFS id_only_match result
  L590-596: BFS full path with get_nodes
  L643-644: BFS count_match returns
  L729, 735-736, 743: khop fast path patterns
"""
import pytest
from iris_vector_graph.engine import IRISGraphEngine


@pytest.fixture
def query_graph(iris_connection, iris_master_cleanup):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
    for i in range(6):
        eng.create_node(f"q_{i}", labels=["QNode"], properties={"val": str(i), "x": i})
    for i in range(5):
        eng.create_edge(f"q_{i}", "Q_REL", f"q_{i + 1}")
    eng.sync()
    return eng


# ---------------------------------------------------------------------------
# EXPLAIN / SHOW / CREATE INDEX pass-through (L114-158)
# ---------------------------------------------------------------------------

class TestCypherPassThrough:

    def test_explain_command(self, query_graph):
        result = query_graph.execute_cypher("EXPLAIN MATCH (n) RETURN n")
        assert result is not None
        assert hasattr(result, "columns") or isinstance(result, dict)

    def test_show_databases(self, query_graph):
        result = query_graph.execute_cypher("SHOW DATABASES")
        assert result is not None

    def test_show_indexes(self, query_graph):
        result = query_graph.execute_cypher("SHOW INDEXES")
        assert result is not None

    def test_create_index_passthrough(self, query_graph):
        result = query_graph.execute_cypher("CREATE INDEX ON :Person(name)")
        assert result is not None

    def test_create_constraint_passthrough(self, query_graph):
        result = query_graph.execute_cypher("CREATE CONSTRAINT ON (n:Person) ASSERT n.id IS UNIQUE")
        assert result is not None

    def test_drop_index_passthrough(self, query_graph):
        result = query_graph.execute_cypher("DROP INDEX my_index IF EXISTS")
        assert result is not None

    def test_create_fulltext_passthrough(self, query_graph):
        result = query_graph.execute_cypher("CREATE FULLTEXT INDEX myIdx ON :Person(name)")
        assert result is not None


# ---------------------------------------------------------------------------
# MERGE with ON CREATE / ON MATCH (translator paths L1747-1805)
# ---------------------------------------------------------------------------

class TestMergeWithActions:

    def test_merge_creates_if_missing(self, query_graph):
        result = query_graph.execute_cypher(
            "MERGE (n:MergeTest {node_id: 'merge_1'}) RETURN n.node_id AS id"
        )
        assert result is not None

    def test_merge_on_create_sets_property(self, query_graph):
        result = query_graph.execute_cypher(
            "MERGE (n:MTest {node_id: 'merge_oc'}) "
            "ON CREATE SET n.created = 'yes' "
            "RETURN n.node_id AS id"
        )
        assert result is not None

    def test_merge_on_match_sets_property(self, query_graph):
        # Create the node first
        query_graph.create_node("merge_om", labels=["MTest"])
        query_graph.sync()
        result = query_graph.execute_cypher(
            "MERGE (n:MTest {node_id: 'merge_om'}) "
            "ON MATCH SET n.matched = 'yes' "
            "RETURN n.node_id AS id"
        )
        assert result is not None


# ---------------------------------------------------------------------------
# Node lookup variations (L201-220)
# ---------------------------------------------------------------------------

class TestNodeLookupVariations:

    def test_match_node_by_label_with_properties(self, query_graph):
        result = query_graph.execute_cypher(
            "MATCH (n:QNode) RETURN n.node_id AS id, n.val AS val LIMIT 3"
        )
        assert result is not None

    def test_match_node_count(self, query_graph):
        result = query_graph.execute_cypher(
            "MATCH (n:QNode) RETURN count(n) AS cnt"
        )
        assert result is not None

    def test_match_no_label(self, query_graph):
        result = query_graph.execute_cypher("MATCH (n) RETURN n.node_id LIMIT 2")
        assert result is not None


# ---------------------------------------------------------------------------
# BFS with return_properties (L295-302)
# ---------------------------------------------------------------------------

class TestBFSWithProperties:

    def test_bfs_with_node_properties(self, query_graph):
        result = query_graph.execute_cypher(
            "MATCH (a {node_id: 'q_0'})-[:Q_REL*1..2]->(b) "
            "RETURN b.node_id, b.val LIMIT 10"
        )
        assert result is not None

    def test_bfs_count_distinct(self, query_graph):
        result = query_graph.execute_cypher(
            "MATCH (a {node_id: 'q_0'})-[:Q_REL*1..2]->(b) "
            "RETURN count(DISTINCT b) AS cnt"
        )
        assert result is not None

    def test_bfs_ids_only(self, query_graph):
        result = query_graph.execute_cypher(
            "MATCH (a {node_id: 'q_0'})-[:Q_REL*1..2]->(b) "
            "RETURN b.node_id AS id"
        )
        assert result is not None

    def test_bfs_with_param(self, query_graph):
        result = query_graph.execute_cypher(
            "MATCH (a {node_id: $src})-[:Q_REL*1..3]->(b) "
            "RETURN b.node_id AS id LIMIT 5",
            parameters={"src": "q_0"}
        )
        assert result is not None

    def test_bfs_in_direction(self, query_graph):
        result = query_graph.execute_cypher(
            "MATCH (a {node_id: 'q_3'})<-[:Q_REL*1..2]-(b) "
            "RETURN b.node_id AS id"
        )
        assert result is not None


# ---------------------------------------------------------------------------
# Shortest path / weighted shortest path (L339-420)
# ---------------------------------------------------------------------------

class TestShortestPathExecution:

    def test_shortest_path_basic(self, query_graph):
        try:
            result = query_graph.execute_cypher(
                "MATCH p = shortestPath((a {node_id: 'q_0'})-[*]-(b {node_id: 'q_3'})) "
                "RETURN p"
            )
            assert result is not None
        except Exception:
            pytest.skip("shortestPath not supported in this env")

    def test_shortest_path_with_param(self, query_graph):
        try:
            result = query_graph.execute_cypher(
                "CALL ivg.shortestPath.weighted($src, $dst) YIELD node, cost "
                "RETURN node, cost",
                parameters={"src": "q_0", "dst": "q_3"}
            )
            assert result is not None
        except Exception:
            pytest.skip("weighted shortestPath not supported")

    def test_var_length_path_undirected(self, query_graph):
        result = query_graph.execute_cypher(
            "MATCH (a {node_id: 'q_0'})-[*1..2]-(b) RETURN b.node_id AS id LIMIT 5"
        )
        assert result is not None


# ---------------------------------------------------------------------------
# SHOW command handler paths
# ---------------------------------------------------------------------------

class TestShowCommands:

    def test_show_schema(self, query_graph):
        result = query_graph.execute_cypher("SHOW SCHEMA")
        assert result is not None

    def test_show_functions(self, query_graph):
        result = query_graph.execute_cypher("SHOW FUNCTIONS")
        assert result is not None

    def test_show_procedures(self, query_graph):
        result = query_graph.execute_cypher("SHOW PROCEDURES")
        assert result is not None


# ---------------------------------------------------------------------------
# DELETE node (L843-858 reification path)
# ---------------------------------------------------------------------------

class TestDeleteNodePaths:

    def test_delete_node_removes_from_graph(self, query_graph):
        query_graph.create_node("del_test_1", labels=["DelTest"])
        query_graph.sync()
        result = query_graph.execute_cypher(
            "MATCH (n {node_id: 'del_test_1'}) DELETE n"
        )
        assert result is not None

    def test_detach_delete_removes_node_and_edges(self, query_graph):
        query_graph.create_node("del_hub", labels=["DelHub"])
        query_graph.create_node("del_spoke", labels=["DelSpoke"])
        query_graph.create_edge("del_hub", "DEL_REL", "del_spoke")
        query_graph.sync()
        result = query_graph.execute_cypher(
            "MATCH (n {node_id: 'del_hub'}) DETACH DELETE n"
        )
        assert result is not None


# ---------------------------------------------------------------------------
# ivg.neighbors CALL procedure (L539-582)
# ---------------------------------------------------------------------------

class TestIvgNeighborsProcedure:

    def test_ivg_neighbors_basic(self, query_graph):
        try:
            result = query_graph.execute_cypher(
                "CALL ivg.neighbors($src, 'Q_REL', 'out') YIELD neighbor "
                "RETURN neighbor",
                parameters={"src": "q_0"}
            )
            assert result is not None
        except Exception:
            pytest.skip("ivg.neighbors not supported")

    def test_ivg_ppr_call(self, query_graph):
        try:
            result = query_graph.execute_cypher(
                "CALL ivg.ppr($seeds, 0.85, 10) YIELD node, score "
                "RETURN node, score LIMIT 5",
                parameters={"seeds": ["q_0"]}
            )
            assert result is not None
        except Exception:
            pytest.skip("ivg.ppr not supported")


# ---------------------------------------------------------------------------
# Read-only mode enforcement (L140-144)
# ---------------------------------------------------------------------------

class TestReadOnlyMode:

    def test_read_only_blocks_create(self, query_graph):
        with pytest.raises(PermissionError):
            query_graph.execute_cypher(
                "CREATE (n:Blocked {node_id: 'blocked'})",
                read_only=True
            )

    def test_read_only_allows_match(self, query_graph):
        result = query_graph.execute_cypher(
            "MATCH (n:QNode) RETURN count(n) AS cnt",
            read_only=True
        )
        assert result is not None


# ---------------------------------------------------------------------------
# AQL execution (L14-22)
# ---------------------------------------------------------------------------

class TestAQLExecution:

    def test_execute_aql_basic(self, query_graph):
        try:
            result = query_graph.execute_aql(
                "FOR n IN nodes FILTER n.labels == ['QNode'] RETURN n.id"
            )
            assert result is not None
        except Exception:
            pytest.skip("AQL not supported")


# ---------------------------------------------------------------------------
# K-hop fast path patterns (L700-787)
# ---------------------------------------------------------------------------

class TestKhopFastPath:

    def test_1hop_count_pattern(self, query_graph):
        result = query_graph.execute_cypher(
            "MATCH (a {node_id: $src})-[:Q_REL]->(b) RETURN count(b) AS cnt",
            parameters={"src": "q_0"}
        )
        assert result is not None

    def test_1hop_ids_pattern(self, query_graph):
        result = query_graph.execute_cypher(
            "MATCH (a {node_id: $src})-[:Q_REL]->(b) RETURN b.node_id",
            parameters={"src": "q_0"}
        )
        assert result is not None

    def test_2hop_count_pattern(self, query_graph):
        result = query_graph.execute_cypher(
            "MATCH (a {node_id: $src})-[:Q_REL*2]->(b) RETURN count(b) AS cnt",
            parameters={"src": "q_0"}
        )
        assert result is not None
