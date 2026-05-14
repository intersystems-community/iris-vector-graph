"""E2E tests gating the four closed openCypher gaps.

Gap 1: IN with string list literals and parameters
Gap 2: MATCH + aggregation + ORDER BY
Gap 3: CALL ivg.bm25/ppr YIELD node column alias
Gap 4: length(p) on named paths (fixed-hop)
"""
import os
import uuid
import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

PREFIX = f"GAP_{uuid.uuid4().hex[:6]}"


class TestCypherGapsE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine
        self.conn = iris_connection
        self.engine = IRISGraphEngine(iris_connection)
        self._setup_test_data()
        yield
        self._cleanup()

    def _setup_test_data(self):
        nodes = [
            (f"{PREFIX}:A", {"category": "protein", "pmid": "38901234"}),
            (f"{PREFIX}:B", {"category": "protein", "pmid": "38765432"}),
            (f"{PREFIX}:C", {"category": "gene",    "pmid": "99999999"}),
        ]
        for nid, props in nodes:
            self.engine.create_node(nid, properties=props)
        self.engine.create_edge(f"{PREFIX}:A", "INTERACTS", f"{PREFIX}:B")
        self.engine.create_edge(f"{PREFIX}:A", "INTERACTS", f"{PREFIX}:C")
        self.engine.create_edge(f"{PREFIX}:B", "INTERACTS", f"{PREFIX}:C")

    def _cleanup(self):
        cur = self.conn.cursor()
        cur.execute(
            f"DELETE FROM Graph_KG.rdf_edges WHERE s LIKE '{PREFIX}%' OR o_id LIKE '{PREFIX}%'"
        )
        cur.execute(f"DELETE FROM Graph_KG.rdf_props WHERE s LIKE '{PREFIX}%'")
        cur.execute(f"DELETE FROM Graph_KG.nodes WHERE node_id LIKE '{PREFIX}%'")
        self.conn.commit()
        cur.close()

    def test_gap1_in_string_literal_list(self):
        q = f'MATCH (n) WHERE n.pmid IN ["38901234", "38765432"] RETURN n.node_id ORDER BY n.node_id'
        r = self.engine.execute_cypher(q)
        node_ids = [row[0] for row in r.rows]
        assert f"{PREFIX}:A" in node_ids
        assert f"{PREFIX}:B" in node_ids
        assert f"{PREFIX}:C" not in node_ids

    def test_gap1_in_string_param_list(self):
        q = "MATCH (n) WHERE n.pmid IN $ids RETURN n.node_id ORDER BY n.node_id"
        r = self.engine.execute_cypher(q, parameters={"ids": ["38901234", "38765432"]})
        node_ids = [row[0] for row in r.rows]
        assert f"{PREFIX}:A" in node_ids
        assert f"{PREFIX}:B" in node_ids
        assert f"{PREFIX}:C" not in node_ids

    def test_gap1_in_empty_list_returns_nothing(self):
        q = "MATCH (n) WHERE n.pmid IN [] RETURN n.node_id"
        r = self.engine.execute_cypher(q)
        assert r.rows == []

    def test_gap2_agg_order_by(self):
        q = f"MATCH (n)-[r]->() WHERE n.node_id STARTS WITH '{PREFIX}' RETURN n.node_id, count(r) AS deg ORDER BY deg DESC"
        r = self.engine.execute_cypher(q)
        assert len(r.rows) >= 1
        degs = [row[1] for row in r.rows]
        assert degs == sorted(degs, reverse=True), "Results not sorted DESC by deg"
        ids = [row[0] for row in r.rows]
        assert f"{PREFIX}:A" in ids
        a_deg = next(row[1] for row in r.rows if row[0] == f"{PREFIX}:A")
        assert a_deg >= 2

    def test_gap4_length_p_1hop(self):
        q = f"MATCH p = (a)-[r]->(b) WHERE a.node_id = '{PREFIX}:A' RETURN length(p) LIMIT 1"
        r = self.engine.execute_cypher(q)
        assert len(r.rows) >= 1
        assert r.rows[0][0] == 1, f"Expected length 1, got {r.rows[0][0]}"

    def test_gap4_length_p_2hop(self):
        q = f"MATCH p = (a)-[r1]->(b)-[r2]->(c) WHERE a.node_id = '{PREFIX}:A' RETURN length(p) LIMIT 1"
        r = self.engine.execute_cypher(q)
        assert len(r.rows) >= 1
        assert r.rows[0][0] == 2, f"Expected length 2, got {r.rows[0][0]}"

    def test_gap4_length_p_matches_hop_count(self):
        q = f"MATCH p = (a)-[r]->(b) WHERE a.node_id STARTS WITH '{PREFIX}' RETURN a.node_id, length(p)"
        r = self.engine.execute_cypher(q)
        assert all(row[1] == 1 for row in r.rows), "All 1-hop paths should have length 1"


class TestCypherYieldColumnAlias:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine
        self.engine = IRISGraphEngine(iris_connection)

    def test_gap3_ppr_yield_no_field_error(self):
        q = "CALL ivg.ppr([\"nonexistent_seed_xyz\"], 0.85, 5) YIELD node, score RETURN node, score"
        r = self.engine.execute_cypher(q)
        assert r.error is None or "NODE" not in (r.error or "").upper() or "not found" not in (r.error or "").lower(), \
            f"Got Field 'NODE' not found error — YIELD alias bug not fixed: {r.error}"

    def test_gap3_bm25_yield_no_field_error(self):
        q = "CALL ivg.bm25.search(\"nonexistent_idx_xyz\", \"test query\", 5) YIELD node, score RETURN node, score"
        r = self.engine.execute_cypher(q)
        assert "node" in r.columns or "node_id" in r.columns, f"Expected node column, got {r.columns}"
        assert "score" in r.columns, f"Expected score column, got {r.columns}"
