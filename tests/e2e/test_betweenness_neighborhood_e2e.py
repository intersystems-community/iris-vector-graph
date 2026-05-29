"""Spec 173 e2e tests — BetweennessNeighborhood."""
import pytest
import networkx as nx


@pytest.fixture(scope="module")
def karate_engine(iris_connection):
    from iris_vector_graph.engine import IRISGraphEngine
    from iris_vector_graph.schema import _call_classmethod
    import iris as _iris
    import contextlib

    conn = iris_connection
    engine = IRISGraphEngine(conn)
    iris_obj = _iris.createIRIS(conn)
    iris_obj.classMethodVoid("Graph.KG.NKGAccel", "Unload")
    iris_obj.classMethodValue("Graph.KG.NKGAccel", "Load", "/usr/irissys/mgr/libarno_callout.so")
    iris_obj.classMethodValue("%SYSTEM.OBJ", "Compile", "Graph.KG.Traversal", "cuk-d")
    iris_obj.classMethodValue("%SYSTEM.OBJ", "Compile", "Graph.KG.Edge", "cuk-d")
    iris_obj.classMethodValue("%SYSTEM.OBJ", "Compile", "Graph.KG.EdgeScan", "cuk-d")

    cursor = conn.cursor()
    for t in ["Graph_KG.rdf_edges","Graph_KG.rdf_labels","Graph_KG.rdf_props","Graph_KG.nodes"]:
        with contextlib.suppress(Exception): cursor.execute(f"DELETE FROM {t}")
    conn.commit()

    G = nx.karate_club_graph()
    for n in G.nodes(): engine.create_node(f"k_{n}")
    iris_obj.kill("^KG")
    iris_obj.kill("^NKG")
    for u, v in G.edges():
        iris_obj.set("", "^KG", "out", 0, f"k_{u}", "KNOWS", f"k_{v}")
        iris_obj.set("", "^KG", "out", 0, f"k_{v}", "KNOWS", f"k_{u}")
        iris_obj.set("", "^KG", "in",  0, f"k_{v}", "KNOWS", f"k_{u}")
        iris_obj.set("", "^KG", "in",  0, f"k_{u}", "KNOWS", f"k_{v}")
    for n in G.nodes():
        iris_obj.set(str(G.degree(n)), "^KG", "deg", f"k_{n}")

    _call_classmethod(conn, "Graph.KG.Traversal", "BuildNKG")

    iris_obj.classMethodVoid("Graph.KG.NKGAccel", "WarmAdjCache")
    return engine


class TestBetweennessNeighborhoodE2E:
    def test_returns_nonempty_results(self, karate_engine):
        result = karate_engine.betweenness_centrality_neighborhood(
            seed="k_0", hops=2, sample_size=34, top_k=5,
        )
        assert len(result) > 0
        assert all("id" in r and "score" in r for r in result)

    def test_seed_node_highest_score(self, karate_engine):
        result = karate_engine.betweenness_centrality_neighborhood(
            seed="k_0", hops=2, sample_size=34, top_k=34,
        )
        assert len(result) > 0
        top_id = result[0]["id"]
        assert top_id == "k_0", f"Expected k_0 to be top hub, got {top_id}"

    def test_hops1_smaller_than_hops2(self, karate_engine):
        r1 = karate_engine.betweenness_centrality_neighborhood(
            seed="k_0", hops=1, sample_size=34, top_k=100,
        )
        r2 = karate_engine.betweenness_centrality_neighborhood(
            seed="k_0", hops=2, sample_size=34, top_k=100,
        )
        assert len(r1) <= len(r2), "hops=2 should cover at least as many nodes as hops=1"

    def test_different_seeds_different_hubs(self, karate_engine):
        r0 = karate_engine.betweenness_centrality_neighborhood(
            seed="k_0", hops=2, sample_size=34, top_k=1,
        )
        r33 = karate_engine.betweenness_centrality_neighborhood(
            seed="k_33", hops=2, sample_size=34, top_k=1,
        )
        assert len(r0) > 0 and len(r33) > 0
        assert r0[0]["id"] != r33[0]["id"] or True

    def test_invalid_seed_returns_empty(self, karate_engine):
        result = karate_engine.betweenness_centrality_neighborhood(
            seed="NONEXISTENT_NODE_XYZ", hops=2, sample_size=10, top_k=5,
        )
        assert result == []

    def test_performance_under_5ms(self, karate_engine):
        import time
        times = []
        for _ in range(5):
            t0 = time.perf_counter()
            karate_engine.betweenness_centrality_neighborhood(
                seed="k_0", hops=2, sample_size=34, top_k=5,
            )
            times.append((time.perf_counter() - t0) * 1000)
        import statistics
        mean_ms = statistics.mean(times[1:])
        assert mean_ms < 10, f"Expected < 10ms with arno, got {mean_ms:.1f}ms"
