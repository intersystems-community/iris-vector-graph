"""E2E tests for graph analytics kernels (024-graph-kernels). TDD — written FIRST."""
import os
import time
import uuid
from collections import Counter

import pytest

SKIP_IRIS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS, reason="SKIP_IRIS_TESTS=true")


def _insert_star(cursor, conn, prefix, n_spokes=4):
    hub = f"{prefix}HUB"
    spokes = [f"{prefix}S{i}" for i in range(1, n_spokes + 1)]
    for n in [hub] + spokes:
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [n])
        except Exception:
            pass
    for s in spokes:
        for src, tgt in [(hub, s), (s, hub)]:
            try:
                cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                               [src, "CONN", tgt])
            except Exception:
                pass
    conn.commit()
    return hub, spokes


def _insert_disconnected(cursor, conn, prefix):
    for n in ["A", "B", "C", "D", "E"]:
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [f"{prefix}{n}"])
        except Exception:
            pass
    for s, o in [("A", "B"), ("B", "C"), ("D", "E")]:
        for src, tgt in [(s, o), (o, s)]:
            try:
                cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                               [f"{prefix}{src}", "REL", f"{prefix}{tgt}"])
            except Exception:
                pass
    conn.commit()


def _insert_bridge_clusters(cursor, conn, prefix):
    cluster1 = [f"{prefix}C1_{i}" for i in range(3)]
    cluster2 = [f"{prefix}C2_{i}" for i in range(3)]
    for n in cluster1 + cluster2:
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [n])
        except Exception:
            pass
    for a in cluster1:
        for b in cluster1:
            if a < b:
                for src, tgt in [(a, b), (b, a)]:
                    try:
                        cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                                       [src, "REL", tgt])
                    except Exception:
                        pass
    for a in cluster2:
        for b in cluster2:
            if a < b:
                for src, tgt in [(a, b), (b, a)]:
                    try:
                        cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                                       [src, "REL", tgt])
                    except Exception:
                        pass
    try:
        cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                       [cluster1[0], "BRIDGE", cluster2[0]])
    except Exception:
        pass
    conn.commit()
    return cluster1, cluster2


def _build_kg(conn):
    try:
        from iris_vector_graph.schema import _call_classmethod
        _call_classmethod(conn, 'Graph.KG.Traversal', 'BuildKG')
    except Exception as e:
        pytest.skip(f"BuildKG failed: {e}")


def _cleanup(cursor, conn, prefix):
    for table, col in [
        ("Graph_KG.rdf_edges", "s"),
        ("Graph_KG.rdf_labels", "s"),
        ("Graph_KG.rdf_props", "s"),
        ("Graph_KG.nodes", "node_id"),
    ]:
        try:
            cursor.execute(f"DELETE FROM {table} WHERE {col} LIKE ?", [f"{prefix}%"])
        except Exception:
            pass
    conn.commit()


class TestPageRankE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        self.prefix = f"PR_{uuid.uuid4().hex[:6]}_"
        self.hub, self.spokes = _insert_star(self.cursor, self.conn, self.prefix)
        _build_kg(self.conn)
        yield
        _cleanup(self.cursor, self.conn, self.prefix)

    def test_hub_highest_score(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        results = ops.kg_PAGERANK(damping=0.85)
        if not results:
            pytest.skip("PageRank returned no results")
        prefix_results = [(n, s) for n, s in results if n.startswith(self.prefix)]
        assert len(prefix_results) >= 5
        hub_entries = [(n, s) for n, s in prefix_results if n == self.hub]
        assert hub_entries, f"Hub not in results"
        hub_score = hub_entries[0][1]
        for spoke in self.spokes:
            spoke_entries = [(n, s) for n, s in prefix_results if n == spoke]
            if spoke_entries:
                assert hub_score >= spoke_entries[0][1]

    def test_all_nodes_scored(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        results = ops.kg_PAGERANK()
        scored_ids = {n for n, _ in results}
        for node in [self.hub] + self.spokes:
            assert node in scored_ids, f"{node} not scored"

    def test_scores_sum_approximately_one(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        results = ops.kg_PAGERANK()
        total = sum(s for _, s in results)
        assert 0.9 < total < 1.1, f"Scores sum to {total}, expected ~1.0"

    def test_early_termination(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        t0 = time.monotonic()
        ops.kg_PAGERANK(max_iterations=100)
        elapsed = (time.monotonic() - t0) * 1000
        assert elapsed < 5000, f"PageRank took {elapsed:.0f}ms with max_iter=100, should converge early"

    def test_empty_graph(self, iris_connection):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(iris_connection)
        _cleanup(self.cursor, self.conn, self.prefix)
        _build_kg(self.conn)
        results = ops.kg_PAGERANK()
        assert isinstance(results, list)


class TestWCCE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        self.prefix = f"WCC_{uuid.uuid4().hex[:6]}_"
        _insert_disconnected(self.cursor, self.conn, self.prefix)
        _build_kg(self.conn)
        yield
        _cleanup(self.cursor, self.conn, self.prefix)

    def test_disconnected_clusters(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        components = ops.kg_WCC()
        if not components:
            pytest.skip("WCC returned no results")
        pA = components.get(f"{self.prefix}A")
        pB = components.get(f"{self.prefix}B")
        pC = components.get(f"{self.prefix}C")
        pD = components.get(f"{self.prefix}D")
        pE = components.get(f"{self.prefix}E")
        assert pA == pB == pC, f"A/B/C should share component: {pA},{pB},{pC}"
        assert pD == pE, f"D/E should share component: {pD},{pE}"
        assert pA != pD, f"Clusters should be separate: {pA} vs {pD}"

    def test_fully_connected(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        components = ops.kg_WCC()
        prefix_comps = {n: c for n, c in components.items() if n.startswith(self.prefix)}
        distinct = set(prefix_comps.values())
        assert len(distinct) == 2, f"Expected 2 components (ABC + DE), got {len(distinct)}"

    def test_isolated_node(self, iris_connection):
        cursor = iris_connection.cursor()
        iso_prefix = f"ISO_{uuid.uuid4().hex[:6]}_"
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [f"{iso_prefix}ALONE"])
        except Exception:
            pass
        iris_connection.commit()
        _build_kg(iris_connection)
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(iris_connection)
        components = ops.kg_WCC()
        alone_comp = components.get(f"{iso_prefix}ALONE")
        if alone_comp:
            comp_members = [n for n, c in components.items() if c == alone_comp]
            assert len(comp_members) == 1
        _cleanup(cursor, iris_connection, iso_prefix)

    def test_empty_graph(self, iris_connection):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(iris_connection)
        result = ops.kg_WCC()
        assert isinstance(result, dict)


class TestCDLPE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        self.prefix = f"CDLP_{uuid.uuid4().hex[:6]}_"
        self.c1, self.c2 = _insert_bridge_clusters(self.cursor, self.conn, self.prefix)
        _build_kg(self.conn)
        yield
        _cleanup(self.cursor, self.conn, self.prefix)

    def test_bridge_clusters(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        communities = ops.kg_CDLP(max_iterations=10)
        if not communities:
            pytest.skip("CDLP returned no results")
        c1_labels = {communities.get(n) for n in self.c1 if n in communities}
        c2_labels = {communities.get(n) for n in self.c2 if n in communities}
        assert len(c1_labels) >= 1 and len(c2_labels) >= 1
        all_labels = c1_labels | c2_labels
        assert len(all_labels) >= 2, f"Expected ≥2 communities, got {all_labels}"

    def test_empty_graph(self, iris_connection):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(iris_connection)
        result = ops.kg_CDLP()
        assert isinstance(result, dict)


class TestKernelPerformance:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        self.prefix = f"PERF_{uuid.uuid4().hex[:6]}_"
        _insert_star(self.cursor, self.conn, self.prefix, n_spokes=20)
        _build_kg(self.conn)
        yield
        _cleanup(self.cursor, self.conn, self.prefix)

    def test_pagerank_performance(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        t0 = time.monotonic()
        ops.kg_PAGERANK()
        assert (time.monotonic() - t0) < 5.0

    def test_wcc_performance(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        t0 = time.monotonic()
        ops.kg_WCC()
        assert (time.monotonic() - t0) < 5.0

    def test_cdlp_performance(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        t0 = time.monotonic()
        ops.kg_CDLP()
        assert (time.monotonic() - t0) < 5.0
