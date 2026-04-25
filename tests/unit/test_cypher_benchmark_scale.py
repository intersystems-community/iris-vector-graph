"""
Benchmark at real scale: 10K nodes, 50K edges.
Compares IVG (Cypher-over-IRIS-SQL) vs published Neo4j/Neptune numbers.
"""
import os, sys, time, statistics, uuid
import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

N_NODES = 10_000
N_EDGES = 50_000
WARM = 5
MEASURED = 20
BATCH = 500


@pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")
class TestCypherBenchmarkScale10K:

    @pytest.fixture(scope="class", autouse=True)
    def seed_data(self, iris_connection):
        import random
        from iris_vector_graph.engine import IRISGraphEngine

        self.__class__.conn = iris_connection
        engine = IRISGraphEngine(iris_connection, embedding_dimension=4)
        engine.initialize_schema()
        self.__class__.engine = engine

        random.seed(42)
        labels = ["Gene", "Drug", "Disease", "Pathway"]
        preds  = ["BINDS", "REGULATES", "ASSOCIATED_WITH", "TREATS", "PATHWAY_MEMBER"]
        run    = "b10k"

        node_ids     = [f"{run}_{i:05d}" for i in range(N_NODES)]
        node_labels  = [labels[i % len(labels)] for i in range(N_NODES)]

        weights = [1.0 / (i + 1) for i in range(N_NODES)]
        total   = sum(weights)
        weights = [w / total for w in weights]

        edges = set()
        while len(edges) < N_EDGES:
            s = random.choices(range(N_NODES), weights=weights)[0]
            o = random.choices(range(N_NODES), weights=weights)[0]
            p = random.choice(preds)
            if s != o:
                edges.add((node_ids[s], p, node_ids[o]))
        edges = list(edges)

        print(f"\nSeeding {N_NODES} nodes + {len(edges)} edges …", flush=True)
        t0 = time.time()
        cursor = iris_connection.cursor()

        for i in range(0, N_NODES, BATCH):
            batch = node_ids[i:i+BATCH]
            lbatch = node_labels[i:i+BATCH]
            for nid, lbl in zip(batch, lbatch):
                cursor.execute(
                    "INSERT INTO Graph_KG.nodes (node_id) "
                    "SELECT ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.nodes WHERE node_id=?)",
                    [nid, nid],
                )
                cursor.execute(
                    "INSERT INTO Graph_KG.rdf_labels (s, label) "
                    "SELECT ?, ? WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_labels WHERE s=? AND label=?)",
                    [nid, lbl, nid, lbl],
                )
            iris_connection.commit()
            if i % 2000 == 0:
                print(f"  nodes {i}/{N_NODES} …", flush=True)

        for i in range(0, len(edges), BATCH):
            batch = edges[i:i+BATCH]
            for s, p, o in batch:
                try:
                    cursor.execute(
                        "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                        [s, p, o],
                    )
                except Exception:
                    pass
            iris_connection.commit()
            if i % 5000 == 0:
                print(f"  edges {i}/{len(edges)} …", flush=True)

        print(f"Seed done in {time.time()-t0:.1f}s", flush=True)
        try:
            engine._iris_obj().classMethodVoid("Graph.KG.Traversal", "BuildKG")
            print("^KG adjacency built.", flush=True)
        except Exception as e:
            print(f"BuildKG failed (BFS may be slow): {e}", flush=True)

        self.__class__.node_ids  = node_ids
        self.__class__.hub       = node_ids[0]
        self.__class__.mid       = node_ids[500]
        self.__class__.leaf      = node_ids[N_NODES - 1]

        yield

        print("\nCleaning up …", flush=True)
        for i in range(0, N_NODES, BATCH):
            batch = node_ids[i:i+BATCH]
            for nid in batch:
                try:
                    cursor.execute("DELETE FROM Graph_KG.rdf_edges WHERE s=? OR o_id=?", [nid, nid])
                    cursor.execute("DELETE FROM Graph_KG.rdf_labels WHERE s=?", [nid])
                    cursor.execute("DELETE FROM Graph_KG.rdf_props  WHERE s=?", [nid])
                    cursor.execute("DELETE FROM Graph_KG.nodes WHERE node_id=?", [nid])
                except Exception:
                    pass
        iris_connection.commit()

    def _bench(self, q, params=None):
        for _ in range(WARM):
            self.engine.execute_cypher(q, params or {})
        times = []
        for _ in range(MEASURED):
            t0 = time.perf_counter()
            result = self.engine.execute_cypher(q, params or {})
            times.append((time.perf_counter() - t0) * 1000)
        return times, result

    def _report(self, name, times, rows=None):
        med = statistics.median(times)
        p95 = sorted(times)[int(len(times) * 0.95)]
        p99 = sorted(times)[int(len(times) * 0.99)]
        print(f"\n  {name}")
        print(f"    p50={med:.1f}ms  p95={p95:.1f}ms  p99={p99:.1f}ms  rows={rows}")
        return med

    def test_01_point_lookup(self):
        q = "MATCH (n) WHERE n.id = $id RETURN n.id, labels(n)"
        times, r = self._bench(q, {"id": self.hub})
        med = self._report("Point lookup — hub node", times, len(r["rows"]))
        assert med < 100

    def test_02_point_lookup_leaf(self):
        q = "MATCH (n) WHERE n.id = $id RETURN n.id, labels(n)"
        times, r = self._bench(q, {"id": self.leaf})
        med = self._report("Point lookup — leaf node", times, len(r["rows"]))
        assert med < 100

    def test_03_1hop_hub(self):
        q = "MATCH (a)-[r:BINDS]->(b) WHERE a.id = $id RETURN b.id, type(r) LIMIT 100"
        times, r = self._bench(q, {"id": self.hub})
        med = self._report("1-hop BINDS from hub (LIMIT 100)", times, len(r["rows"]))
        assert med < 500

    def test_04_1hop_any_pred(self):
        q = "MATCH (a)-[r]->(b) WHERE a.id = $id RETURN b.id, type(r)"
        times, r = self._bench(q, {"id": self.hub})
        med = self._report("1-hop any predicate from hub", times, len(r["rows"]))
        assert med < 500

    def test_05_2hop(self):
        q = "MATCH (a)-[r1]->(b)-[r2]->(c) WHERE a.id = $id RETURN c.id LIMIT 200"
        times, r = self._bench(q, {"id": self.mid})
        med = self._report("2-hop from mid-degree node (LIMIT 200)", times, len(r["rows"]))
        assert med < 2000

    def test_06_label_filter_10k(self):
        q = "MATCH (n:Gene) WHERE n.id STARTS WITH $p RETURN n.id LIMIT 50"
        times, r = self._bench(q, {"p": "b10k_0"})
        med = self._report("Label filter Gene + STARTS WITH (LIMIT 50)", times, len(r["rows"]))
        assert med < 500

    def test_07_in_list_50(self):
        ids = self.node_ids[:50]
        q = "MATCH (n) WHERE n.id IN $ids RETURN n.id"
        times, r = self._bench(q, {"ids": ids})
        med = self._report("IN list — 50 ids", times, len(r["rows"]))
        assert med < 500

    def test_08_aggregation_degree(self):
        ids = self.node_ids[:20]
        q = "MATCH (n)-[r]->(m) WHERE n.id IN $ids WITH n, count(r) AS deg RETURN n.id, deg ORDER BY deg DESC"
        times, r = self._bench(q, {"ids": ids})
        med = self._report("Degree aggregation — 20 hubs", times, len(r["rows"]))
        assert med < 1000

    def test_09_with_having(self):
        ids = self.node_ids[:30]
        q = "MATCH (n)-[r]->(m) WHERE n.id IN $ids WITH n, count(r) AS deg WHERE deg >= 2 RETURN n.id, deg ORDER BY deg DESC"
        times, r = self._bench(q, {"ids": ids})
        med = self._report("WITH HAVING deg >= 2 — 30 nodes", times, len(r["rows"]))
        assert med < 1000

    def test_10_bfs_hub(self):
        q = "MATCH (a)-[r*1..2]->(b) WHERE a.id = $id RETURN b.id LIMIT 500"
        times, r = self._bench(q, {"id": self.hub})
        med = self._report("BFS 1..2 hops from hub (LIMIT 500)", times, len(r["rows"]))
        assert med < 5000

    def test_11_bfs_mid(self):
        q = "MATCH (a)-[r*1..3]->(b) WHERE a.id = $id RETURN b.id LIMIT 200"
        times, r = self._bench(q, {"id": self.mid})
        med = self._report("BFS 1..3 hops from mid node (LIMIT 200)", times, len(r["rows"]))
        assert med < 5000

    def test_12_union(self):
        q = "MATCH (n:Gene) WHERE n.id STARTS WITH $p RETURN n.id LIMIT 25 UNION MATCH (n:Drug) WHERE n.id STARTS WITH $p RETURN n.id LIMIT 25"
        times, r = self._bench(q, {"p": "b10k_0"})
        med = self._report("UNION Gene + Drug (25 each)", times, len(r["rows"]))
        assert med < 500

    def test_summary(self):
        print("\n\n" + "═" * 60)
        print("  IVG BENCHMARK @ 10K NODES / 50K EDGES")
        print("═" * 60)
        print(f"  {WARM} warm-up + {MEASURED} measured runs per query")
        print(f"  Power-law degree distribution (hub: ~{N_EDGES//N_NODES*5}+ edges)")
        print("═" * 60)
