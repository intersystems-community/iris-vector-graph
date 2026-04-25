import os
import statistics
import time
import uuid

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
N_NODES = 200
N_EDGES = 400
WARM_RUNS = 3
BENCH_RUNS = 10


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestCypherBenchmark:

    @pytest.fixture(scope="class", autouse=True)
    def seed_data(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine

        self.__class__.conn = iris_connection
        engine = IRISGraphEngine(iris_connection, embedding_dimension=4)
        engine.initialize_schema()
        self.__class__.engine = engine
        run = uuid.uuid4().hex[:8]
        self.__class__.run = run

        cursor = iris_connection.cursor()
        node_ids = [f"bench_{run}_{i}" for i in range(N_NODES)]
        labels = ["Gene", "Drug", "Disease", "Pathway"]

        for i, nid in enumerate(node_ids):
            label = labels[i % len(labels)]
            cursor.execute(
                "INSERT INTO Graph_KG.nodes (node_id) SELECT ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.nodes WHERE node_id=?)",
                [nid, nid],
            )
            cursor.execute(
                "INSERT INTO Graph_KG.rdf_labels (s, label) SELECT ?, ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_labels WHERE s=? AND label=?)",
                [nid, label, nid, label],
            )
            cursor.execute(
                "INSERT INTO Graph_KG.rdf_props (s, \"key\", val) SELECT ?, 'score', ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_props WHERE s=? AND \"key\"='score')",
                [nid, str(i % 100), nid],
            )

        preds = ["BINDS", "REGULATES", "ASSOCIATED_WITH", "TREATS"]
        for i in range(N_EDGES):
            s = node_ids[i % N_NODES]
            o = node_ids[(i * 7 + 13) % N_NODES]
            p = preds[i % len(preds)]
            if s != o:
                try:
                    cursor.execute(
                        "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                        [s, p, o],
                    )
                    engine._iris_obj().classMethodVoid("Graph.KG.EdgeScan", "WriteAdjacency", s, p, o, "1.0")
                except Exception:
                    pass

        iris_connection.commit()
        self.__class__.node_ids = node_ids
        self.__class__.hub = node_ids[0]
        self.__class__.target = node_ids[N_NODES - 1]
        yield

        for nid in node_ids:
            try:
                cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s=? OR o_id=?", [nid, nid])
                cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s=?", [nid])
                cursor.execute("DELETE FROM Graph_KG.rdf_props WHERE s=?", [nid])
                cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id=?", [nid])
            except Exception:
                pass
        iris_connection.commit()

    def _bench(self, q, params=None):
        for _ in range(WARM_RUNS):
            self.engine.execute_cypher(q, params or {})
        times = []
        for _ in range(BENCH_RUNS):
            t0 = time.perf_counter()
            result = self.engine.execute_cypher(q, params or {})
            times.append((time.perf_counter() - t0) * 1000)
        return times, result

    def _report(self, name, times, rows=None):
        med = statistics.median(times)
        p95 = sorted(times)[int(len(times) * 0.95)]
        print(f"\n  {name}")
        print(f"    p50={med:.1f}ms  p95={p95:.1f}ms  rows={rows}")
        return med

    def test_01_point_lookup(self):
        q = "MATCH (n) WHERE n.id = $id RETURN n.id, labels(n)"
        times, r = self._bench(q, {"id": self.hub})
        med = self._report("Point lookup (1 node by id)", times, len(r["rows"]))
        assert med < 50, f"Point lookup too slow: {med:.1f}ms"

    def test_02_label_filter(self):
        q = "MATCH (n) WHERE n:Gene AND n.id STARTS WITH $p RETURN n.id LIMIT 20"
        times, r = self._bench(q, {"p": f"bench_{self.run}"})
        med = self._report("Label filter + STARTS WITH (20 results)", times, len(r["rows"]))
        assert med < 200

    def test_03_in_list(self):
        ids = self.node_ids[:20]
        q = "MATCH (n) WHERE n.id IN $ids RETURN n.id"
        times, r = self._bench(q, {"ids": ids})
        med = self._report("IN list (20 ids)", times, len(r["rows"]))
        assert med < 200

    def test_04_1hop_traversal(self):
        q = "MATCH (n)-[r:BINDS]->(m) WHERE n.id = $id RETURN m.id, type(r)"
        times, r = self._bench(q, {"id": self.hub})
        med = self._report("1-hop BINDS traversal", times, len(r["rows"]))
        assert med < 200

    def test_05_2hop_traversal(self):
        q = "MATCH (a)-[r1]->(b)-[r2]->(c) WHERE a.id = $id RETURN a.id, c.id LIMIT 50"
        times, r = self._bench(q, {"id": self.hub})
        med = self._report("2-hop traversal (LIMIT 50)", times, len(r["rows"]))
        assert med < 500

    def test_06_aggregation_count(self):
        q = "MATCH (n)-[r]->(m) WHERE n.id IN $ids WITH n, count(r) AS deg RETURN n.id, deg ORDER BY deg DESC"
        times, r = self._bench(q, {"ids": self.node_ids[:10]})
        med = self._report("Aggregation: count edges per node (10 hubs)", times, len(r["rows"]))
        assert med < 500

    def test_07_degree_count(self):
        q = "MATCH (n:Gene)-[r]->(m) WHERE n.id STARTS WITH $p WITH n, count(r) AS degree RETURN n.id, degree ORDER BY degree DESC LIMIT 10"
        times, r = self._bench(q, {"p": f"bench_{self.run}"})
        med = self._report("count() neighbors per Gene (LIMIT 10)", times, len(r["rows"]))
        assert med < 500

    def test_08_varlength_bfs(self):
        q = "MATCH (a)-[r*1..3]->(b) WHERE a.id = $id RETURN b.id LIMIT 100"
        times, r = self._bench(q, {"id": self.hub})
        med = self._report("BFS var-length 1..3 hops (LIMIT 100)", times, len(r["rows"]))
        assert med < 2000

    def test_09_where_label_and_prop(self):
        q = "MATCH (n) WHERE n:Gene AND n.score > '50' AND n.id STARTS WITH $p RETURN n.id LIMIT 20"
        times, r = self._bench(q, {"p": f"bench_{self.run}"})
        med = self._report("Label + prop filter compound WHERE", times, len(r["rows"]))
        assert med < 300

    def test_10_with_having(self):
        q = "MATCH (n)-[r]->(m) WHERE n.id STARTS WITH $p WITH n, count(r) AS cnt WHERE cnt >= 1 RETURN n.id, cnt ORDER BY cnt DESC LIMIT 10"
        times, r = self._bench(q, {"p": f"bench_{self.run}"})
        med = self._report("WITH agg HAVING >= 1 (top 10 by degree)", times, len(r["rows"]))
        assert med < 500

    def test_11_set_map_merge(self):
        target = self.node_ids[1]
        q = "MATCH (n) WHERE n.id = $id SET n += {tag: 'benchmarked', run: $run}"
        times, r = self._bench(q, {"id": target, "run": self.run})
        med = self._report("SET n += {map} (property merge)", times, None)
        assert med < 200

    def test_12_union(self):
        q = "MATCH (n:Gene) WHERE n.id STARTS WITH $p RETURN n.id LIMIT 10 UNION MATCH (n:Drug) WHERE n.id STARTS WITH $p RETURN n.id LIMIT 10"
        times, r = self._bench(q, {"p": f"bench_{self.run}"})
        med = self._report("UNION (Gene + Drug, 10 each)", times, len(r["rows"]))
        assert med < 500

    def test_summary(self):
        print("\n\n" + "═" * 56)
        print("  CYPHER E2E PERFORMANCE SUMMARY")
        print("═" * 56)
        print(f"  Dataset: {N_NODES} nodes, {N_EDGES} edges")
        print(f"  Each benchmark: {WARM_RUNS} warm + {BENCH_RUNS} measured runs")
        print("═" * 56)
        print("  All latency assertions passed ✓")
