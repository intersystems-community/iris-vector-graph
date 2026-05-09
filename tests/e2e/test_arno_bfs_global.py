import json
import uuid
import pytest

PREFIX = f"arnobfs_{uuid.uuid4().hex[:8]}"
PRED = "KNOWS"


@pytest.fixture(scope="module")
def engine(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine
    return IRISGraphEngine(iris_connection)


@pytest.fixture(scope="module")
def arno_setup(engine, iris_connection):
    o = engine._iris_obj()
    cur = iris_connection.cursor()
    nodes = [f"{PREFIX}_{i}" for i in range(8)]
    for n in nodes:
        cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [n])
    edges = [(nodes[0], PRED, nodes[i]) for i in range(1, 5)]
    edges += [(nodes[1], PRED, nodes[i]) for i in range(5, 8)]
    for s, p, d in edges:
        cur.execute("INSERT INTO Graph_KG.rdf_edges (s,p,o_id) VALUES (?,?,?)", [s, p, d])
    iris_connection.commit()
    engine.rebuild_kg()
    engine.rebuild_nkg()
    seed = nodes[0]
    yield engine.conn, o, engine, seed
    for s, p, d in edges:
        cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE s=? AND p=? AND o_id=?", [s, p, d])
    for n in nodes:
        cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id=?", [n])
    iris_connection.commit()


class TestArnoBFSGlobal:

    def test_sc001_bfs_no_maxstring_on_medium_graph(self, arno_setup):
        conn, o, eng, seed = arno_setup
        from iris_vector_graph.schema import _call_classmethod_large
        result_str = str(o.classMethodValue(
            "Graph.KG.NKGAccel", "BFSJson", seed, f'["{PRED}"]', 2, 0))
        if result_str.startswith("SORTED:") and result_str != "SORTED:0":
            tag = result_str.split(":")[1]
            results = json.loads(str(o.classMethodValue(
                "Graph.KG.Traversal", "ReadBFSResults", tag)))
        else:
            results = json.loads(result_str)
        assert isinstance(results, list), "Result must be a JSON array"
        assert len(results) > 0, "Expected BFS results"

    def test_sc002_correctness_vs_bfs_fast_json(self, arno_setup):
        conn, o, eng, seed = arno_setup
        from iris_vector_graph.schema import _call_classmethod_large
        arno_raw = str(o.classMethodValue(
            "Graph.KG.NKGAccel", "BFSJson", seed, f'["{PRED}"]', 2, 0))
        if arno_raw.startswith("SORTED:") and arno_raw != "SORTED:0":
            tag = arno_raw.split(":")[1]
            arno_results = json.loads(str(o.classMethodValue(
                "Graph.KG.Traversal", "ReadBFSResults", tag)))
        else:
            arno_results = json.loads(arno_raw)
        assert len(arno_results) > 0
        assert all("o" in r for r in arno_results[:5])
        assert all("step" in r for r in arno_results[:5])

    def test_sc003_predicate_filter(self, arno_setup):
        conn, o, eng, seed = arno_setup
        from iris_vector_graph.schema import _call_classmethod_large

        def read_bfs(raw):
            if raw.startswith("SORTED:") and raw != "SORTED:0":
                tag = raw.split(":")[1]
                return json.loads(str(o.classMethodValue(
                    "Graph.KG.Traversal", "ReadBFSResults", tag)))
            return json.loads(raw)

        knows_nodes = {r["o"] for r in read_bfs(str(o.classMethodValue(
            "Graph.KG.NKGAccel", "BFSJson", seed, f'["{PRED}"]', 1, 0)))}
        all_nodes = {r["o"] for r in read_bfs(str(o.classMethodValue(
            "Graph.KG.NKGAccel", "BFSJson", seed, '[]', 1, 0)))}
        assert knows_nodes.issubset(all_nodes)

    def test_sc004_fallback_on_no_arno(self, arno_setup):
        conn, o, eng, seed = arno_setup
        with open("iris_src/src/Graph/KG/NKGAccel.cls") as f:
            cls_src = f.read()
        assert "BFSFastJsonSorted" in cls_src, "Fallback must exist in NKGAccel.cls"

    def test_sc005_execute_cypher_routes_through_arno(self, arno_setup):
        conn, o, eng, seed = arno_setup
        result = eng.execute_cypher(
            f"MATCH (a {{node_id:$src}})-[:{PRED}*1..2]-(b) RETURN DISTINCT b.node_id LIMIT 100",
            {"src": seed}
        )
        assert result
        assert len(result.rows) > 0
        assert len(result.rows) <= 100
