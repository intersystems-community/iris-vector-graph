import json
import time
import uuid

import pytest

PREFIX = f"ic13_{uuid.uuid4().hex[:8]}"
PRED = "KNOWS"


@pytest.fixture(scope="module")
def engine(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine
    return IRISGraphEngine(iris_connection)


@pytest.fixture(scope="module")
def graph(engine, iris_connection):
    import iris as _iris
    o = _iris.createIRIS(iris_connection)
    cur = iris_connection.cursor()
    nodes = [f"{PREFIX}_{i}" for i in range(10)]
    for n in nodes:
        cur.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [n])
    edges = [
        (nodes[0], PRED, nodes[1]),
        (nodes[1], PRED, nodes[2]),
        (nodes[2], PRED, nodes[3]),
        (nodes[0], PRED, nodes[4]),
        (nodes[4], PRED, nodes[5]),
        (nodes[5], PRED, nodes[6]),
    ]
    for s, p, d in edges:
        cur.execute("INSERT INTO Graph_KG.rdf_edges (s,p,o_id) VALUES (?,?,?)", [s, p, d])
    iris_connection.commit()
    engine.rebuild_kg()
    engine.rebuild_nkg()
    yield nodes, o
    for s, p, d in edges:
        cur.execute("DELETE FROM Graph_KG.rdf_edges WHERE s=? AND p=? AND o_id=?", [s, p, d])
    for n in nodes:
        cur.execute("DELETE FROM Graph_KG.nodes WHERE node_id=?", [n])
    iris_connection.commit()


class TestIC13ShortestPath:

    def test_sc001_method_exists(self, engine, graph):
        nodes, o = graph
        exists = str(o.classMethodValue(
            "%Dictionary.CompiledMethod", "%ExistsId",
            "Graph.KG.NKGAccel||ShortestPathNKG"
        ))
        assert exists == "1", "ShortestPathNKG must be compiled into NKGAccel"

    def test_sc002_correctness_matches_existing(self, engine, graph):
        nodes, o = graph
        pairs = [
            (nodes[0], nodes[3]),
            (nodes[0], nodes[6]),
            (nodes[1], nodes[3]),
        ]
        for src, dst in pairs:
            old = json.loads(str(o.classMethodValue(
                "Graph.KG.Traversal", "ShortestPathJson", src, dst, 10, "[]", "out", 0)))
            new_raw = str(o.classMethodValue(
                "Graph.KG.NKGAccel", "ShortestPathNKG", src, dst, 10))
            new = json.loads(new_raw)
            old_hops = old[0]["length"] if old else -1
            new_hops = new.get("hops", -2)
            assert old_hops == new_hops, f"{src}->{dst} old={old_hops} new={new_hops}"

    def test_sc003_3hop_under_20ms(self, engine, graph):
        nodes, o = graph
        src, dst = nodes[0], nodes[6]
        times = []
        for _ in range(10):
            t0 = time.perf_counter()
            o.classMethodValue("Graph.KG.NKGAccel", "ShortestPathNKG", src, dst, 10)
            times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        p50 = times[5]
        assert p50 <= 100, f"ShortestPathNKG p50={p50:.1f}ms > 100ms target"

    def test_sc004_no_path_returns_minus1(self, engine, graph):
        nodes, o = graph
        result = json.loads(str(o.classMethodValue(
            "Graph.KG.NKGAccel", "ShortestPathNKG",
            "nonexistent_xyz_1", "nonexistent_xyz_2", 10)))
        assert result.get("hops") == -1, "nonexistent nodes must return hops=-1"

    def test_sc005_same_node_returns_0(self, engine, graph):
        nodes, o = graph
        result = json.loads(str(o.classMethodValue(
            "Graph.KG.NKGAccel", "ShortestPathNKG", nodes[0], nodes[0], 10)))
        assert result.get("hops") == 0, "same src and dst must return hops=0"

    def test_sc006_far_pair_under_200ms(self, engine, graph):
        nodes, o = graph
        src, dst = nodes[0], nodes[6]
        times = []
        for _ in range(5):
            t0 = time.perf_counter()
            o.classMethodValue("Graph.KG.NKGAccel", "ShortestPathNKG", src, dst, 10)
            times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        p50 = times[2]
        assert p50 <= 200, f"ShortestPathNKG p50={p50:.1f}ms > 200ms target"
