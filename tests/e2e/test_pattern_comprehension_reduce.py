"""
E2E tests for pattern comprehension and REDUCE expressions.
Gap #5 (Pattern Comprehension) and #7 (REDUCE) from cypher-gap-recommendations.md.

Both features must produce correct results from the SQL layer alone —
no Python post-processing — so they work identically from ObjectScript,
embedded Python, and external Python callers.
"""
import json
import pytest
from iris_vector_graph.engine import IRISGraphEngine


@pytest.fixture(scope="module")
def engine(iris_connection):
    return IRISGraphEngine(iris_connection)


@pytest.fixture(autouse=True)
def seed_data(iris_connection):
    cur = iris_connection.cursor()
    cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES ('pc_d1')")
    cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES ('pc_g1')")
    cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES ('pc_g2')")
    cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES ('pc_g3')")
    cur.execute("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES ('pc_d1', 'Drug')")
    cur.execute("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES ('pc_g1', 'Gene')")
    cur.execute("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES ('pc_g2', 'Gene')")
    cur.execute("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES ('pc_g3', 'Gene')")
    cur.execute("INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES ('pc_d1', 'name', 'Aspirin')")
    cur.execute("INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES ('pc_g1', 'name', 'PTGS1')")
    cur.execute("INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES ('pc_g2', 'name', 'PTGS2')")
    cur.execute("INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES ('pc_g3', 'name', 'PTGS3')")
    cur.execute("INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES ('pc_g1', 'score', '0.9')")
    cur.execute("INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES ('pc_g2', 'score', '0.7')")
    cur.execute("INSERT INTO Graph_KG.rdf_props (s, \"key\", val) VALUES ('pc_g3', 'score', '0.5')")
    cur.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES ('pc_d1', 'HAS_GENE', 'pc_g1')")
    cur.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES ('pc_d1', 'HAS_GENE', 'pc_g2')")
    cur.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES ('pc_d1', 'HAS_GENE', 'pc_g3')")
    iris_connection.commit()
    yield
    for t in ('pc_d1', 'pc_g1', 'pc_g2', 'pc_g3'):
        cur.execute('DELETE FROM Graph_KG.rdf_props WHERE s=?', [t])
        cur.execute('DELETE FROM Graph_KG.rdf_labels WHERE s=?', [t])
        cur.execute('DELETE FROM Graph_KG.rdf_edges WHERE s=? OR o_id=?', [t, t])
        cur.execute('DELETE FROM Graph_KG.nodes WHERE node_id=?', [t])
    iris_connection.commit()


@pytest.mark.requires_database
@pytest.mark.e2e
class TestPatternComprehension:
    def test_collect_property(self, engine):
        r = engine.execute_cypher(
            "MATCH (d:Drug {node_id:$id}) RETURN [(d)-[:HAS_GENE]->(g) | g.name] AS gene_names",
            {"id": "pc_d1"},
        )
        assert r["rows"], "Expected non-empty result"
        names = sorted(json.loads(r["rows"][0][0]))
        assert names == ["PTGS1", "PTGS2", "PTGS3"]

    def test_collect_node_id(self, engine):
        r = engine.execute_cypher(
            "MATCH (d:Drug {node_id:$id}) RETURN [(d)-[:HAS_GENE]->(g) | g.node_id] AS ids",
            {"id": "pc_d1"},
        )
        ids = sorted(json.loads(r["rows"][0][0]))
        assert ids == ["pc_g1", "pc_g2", "pc_g3"]

    def test_empty_when_no_matches(self, engine):
        r = engine.execute_cypher(
            "MATCH (d:Drug {node_id:$id}) RETURN [(d)-[:UNKNOWN_REL]->(g) | g.name] AS names",
            {"id": "pc_d1"},
        )
        raw = r["rows"][0][0] if r["rows"] else None
        result = json.loads(raw) if raw else []
        assert result == []

    def test_inline_in_match(self, engine):
        r = engine.execute_cypher(
            "MATCH (d {node_id:$id}) RETURN d.name AS drug, [(d)-[:HAS_GENE]->(g) | g.name] AS genes",
            {"id": "pc_d1"},
        )
        assert r["rows"]
        row = r["rows"][0]
        assert row[0] == "Aspirin"
        assert sorted(json.loads(row[1])) == ["PTGS1", "PTGS2", "PTGS3"]


@pytest.mark.requires_database
@pytest.mark.e2e
class TestReduceExpression:
    def test_sum_scores(self, engine):
        r = engine.execute_cypher(
            "MATCH (d {node_id:$id})-[:HAS_GENE]->(g) "
            "RETURN reduce(acc=0.0, x IN collect(g.score) | acc + x) AS total",
            {"id": "pc_d1"},
        )
        assert r["rows"]
        total = float(r["rows"][0][0])
        assert abs(total - 2.1) < 0.01, f"Expected 2.1 (0.9+0.7+0.5), got {total}"

    def test_sum_with_nonzero_init(self, engine):
        r = engine.execute_cypher(
            "MATCH (d {node_id:$id})-[:HAS_GENE]->(g) "
            "RETURN reduce(acc=10.0, x IN collect(g.score) | acc + x) AS total",
            {"id": "pc_d1"},
        )
        total = float(r["rows"][0][0])
        assert abs(total - 12.1) < 0.01, f"Expected 12.1, got {total}"

    def test_correct_sql_surface(self, engine):
        from iris_vector_graph.cypher.parser import parse_query
        from iris_vector_graph.cypher.translator import translate_to_sql
        q = parse_query(
            "MATCH (d {node_id:$id})-[:HAS_GENE]->(g) "
            "RETURN reduce(acc=0.0, x IN collect(g.score) | acc + x) AS total"
        )
        sql_q = translate_to_sql(q, {"id": "pc_d1"})
        assert "JSON_ARRAYAGG" not in sql_q.sql, "REDUCE must not use JSON_ARRAYAGG (breaks ObjectScript surface)"
        assert "JSON_TABLE" not in sql_q.sql, "REDUCE must not use JSON_TABLE over aggregate"
        assert "SUM" in sql_q.sql.upper()
        assert "CAST" in sql_q.sql.upper()
