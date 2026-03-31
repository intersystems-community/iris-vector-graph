"""E2E tests for operator wiring fixes (022-wire-up-operators)."""
import json
import os
import time
import uuid

import pytest

SKIP_IRIS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS, reason="SKIP_IRIS_TESTS=true")


def _insert_chain(cursor, conn, prefix):
    nodes = [f"{prefix}A", f"{prefix}B", f"{prefix}C"]
    for n in nodes:
        cursor.execute(
            "INSERT INTO Graph_KG.nodes (node_id) SELECT ? WHERE NOT EXISTS "
            "(SELECT 1 FROM Graph_KG.nodes WHERE node_id = ?)", [n, n])
        cursor.execute(
            "INSERT INTO Graph_KG.rdf_labels (s, label) SELECT ?, 'Entity' WHERE NOT EXISTS "
            "(SELECT 1 FROM Graph_KG.rdf_labels WHERE s = ? AND label = 'Entity')", [n, n])
    for s, o in [(f"{prefix}A", f"{prefix}B"), (f"{prefix}B", f"{prefix}C")]:
        cursor.execute(
            "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) SELECT ?, 'REL', ? WHERE NOT EXISTS "
            "(SELECT 1 FROM Graph_KG.rdf_edges WHERE s = ? AND p = 'REL' AND o_id = ?)",
            [s, o, s, o])
    conn.commit()
    return nodes


def _insert_star(cursor, conn, prefix, n_spokes=4):
    hub = f"{prefix}HUB"
    spokes = [f"{prefix}S{i}" for i in range(1, n_spokes + 1)]
    for n in [hub] + spokes:
        cursor.execute(
            "INSERT INTO Graph_KG.nodes (node_id) SELECT ? WHERE NOT EXISTS "
            "(SELECT 1 FROM Graph_KG.nodes WHERE node_id = ?)", [n, n])
    for spoke in spokes:
        for s, o in [(hub, spoke), (spoke, hub)]:
            cursor.execute(
                "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) SELECT ?, 'CONN', ? WHERE NOT EXISTS "
                "(SELECT 1 FROM Graph_KG.rdf_edges WHERE s = ? AND p = 'CONN' AND o_id = ?)",
                [s, o, s, o])
    conn.commit()
    return hub, spokes


def _build_kg(conn):
    try:
        from iris_vector_graph.schema import _call_classmethod
        _call_classmethod(conn, 'Graph.KG.Traversal', 'BuildKG')
    except Exception as e:
        pytest.skip(f"BuildKG failed (ObjectScript may not be deployed): {e}")


def _cleanup(cursor, conn, prefix):
    for table, col in [
        ("Graph_KG.kg_NodeEmbeddings", "id"),
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


class TestKgGraphWalkE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        self.prefix = f"GW_{uuid.uuid4().hex[:6]}_"
        _insert_chain(self.cursor, self.conn, self.prefix)
        _build_kg(self.conn)
        yield
        _cleanup(self.cursor, self.conn, self.prefix)

    def test_returns_multihop_results(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        results = ops.kg_GRAPH_WALK(f"{self.prefix}A", max_depth=2)
        targets = {r[2] for r in results}
        assert f"{self.prefix}B" in targets, f"B not reached: {results}"
        assert f"{self.prefix}C" in targets, f"C not reached at depth 2: {results}"

    def test_sql_fallback_works(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        results = ops.kg_GRAPH_WALK(f"{self.prefix}A", max_depth=1)
        assert len(results) >= 1

    def test_nonexistent_entity_returns_empty(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        assert ops.kg_GRAPH_WALK(f"{self.prefix}NOPE", max_depth=2) == []


class TestKgPPRE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        self.prefix = f"PPR_{uuid.uuid4().hex[:6]}_"
        self.hub, self.spokes = _insert_star(self.cursor, self.conn, self.prefix)
        _build_kg(self.conn)
        yield
        _cleanup(self.cursor, self.conn, self.prefix)

    def test_hub_highest_score(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        results = ops.kg_PPR(seed_entities=[self.spokes[0]], damping=0.85)
        if not results:
            pytest.skip("PPR returned no results (ObjectScript may not be deployed)")
        hub_entries = [(n, s) for n, s in results if n == self.hub]
        assert hub_entries, f"Hub not in results: {results[:5]}"
        hub_score = hub_entries[0][1]
        for spoke in self.spokes[1:]:
            spoke_entries = [(n, s) for n, s in results if n == spoke]
            if spoke_entries:
                assert hub_score >= spoke_entries[0][1]

    def test_empty_seeds(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        assert ops.kg_PPR(seed_entities=[]) == []

    def test_completes_under_5s(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        t0 = time.monotonic()
        ops.kg_PPR(seed_entities=[self.spokes[0]])
        assert (time.monotonic() - t0) < 5.0


class TestKgPPRSqlFunction:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        self.prefix = f"PSQL_{uuid.uuid4().hex[:6]}_"
        self.hub, self.spokes = _insert_star(self.cursor, self.conn, self.prefix, n_spokes=3)
        _build_kg(self.conn)
        yield
        _cleanup(self.cursor, self.conn, self.prefix)

    def test_returns_valid_json(self):
        seed_json = json.dumps([self.spokes[0]])
        try:
            self.cursor.execute("SELECT Graph_KG.kg_PPR(?, 0.85, 20, 0, 1.0)", [seed_json])
            row = self.cursor.fetchone()
        except Exception as e:
            pytest.skip(f"kg_PPR SQL function not available: {e}")
        assert row and row[0], "kg_PPR returned empty"
        parsed = json.loads(row[0])
        assert isinstance(parsed, list)
        if parsed:
            assert "id" in parsed[0] and "score" in parsed[0]


class TestKgKNNVECE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        self.prefix = f"VEC_{uuid.uuid4().hex[:6]}_"
        self._insert_embeddings()
        yield
        _cleanup(self.cursor, self.conn, self.prefix)

    def _insert_embeddings(self):
        import numpy as np
        rng = np.random.default_rng(42)
        for i in range(5):
            nid = f"{self.prefix}N{i}"
            self.cursor.execute(
                "INSERT INTO Graph_KG.nodes (node_id) SELECT ? WHERE NOT EXISTS "
                "(SELECT 1 FROM Graph_KG.nodes WHERE node_id = ?)", [nid, nid])
            vec = rng.normal(0, 1, 768).tolist()
            vec_str = ",".join(f"{v:.6f}" for v in vec)
            self.cursor.execute(
                "INSERT INTO Graph_KG.kg_NodeEmbeddings (id, emb) SELECT ?, TO_VECTOR(?, DOUBLE) "
                "WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.kg_NodeEmbeddings WHERE id = ?)",
                [nid, vec_str, nid])
        self.conn.commit()

    def test_returns_results(self):
        import numpy as np
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        query = json.dumps(np.random.default_rng(42).normal(0, 1, 768).tolist())
        results = ops.kg_KNN_VEC(query, k=3)
        assert len(results) > 0
        prefixed = [nid for nid, _ in results if nid.startswith(self.prefix)]
        assert len(prefixed) > 0, f"Expected at least one result with prefix {self.prefix}, got {[nid for nid, _ in results]}"

    def test_node_id_input(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        node_id = f"{self.prefix}N0"
        results = ops.kg_KNN_VEC(node_id, k=3)
        assert len(results) > 0
        assert all(nid != node_id for nid, _ in results)
        assert all(isinstance(sim, float) for _, sim in results)

    def test_no_fallback_warning(self, caplog):
        import logging
        import numpy as np
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        query = json.dumps(np.random.default_rng(99).normal(0, 1, 768).tolist())
        with caplog.at_level(logging.WARNING, logger="iris_vector_graph.operators"):
            ops.kg_KNN_VEC(query, k=3)
        fallbacks = [r for r in caplog.records if "fallback" in r.message.lower() or "falling back" in r.message.lower()]
        assert len(fallbacks) == 0, f"HNSW fell back: {[r.message for r in fallbacks]}"


class TestKgNeighborsE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        self.prefix = f"NBR_{uuid.uuid4().hex[:6]}_"
        self._insert_data()
        yield
        _cleanup(self.cursor, self.conn, self.prefix)

    def _insert_data(self):
        for n in ["A1", "A2", "E1", "E2", "E3", "X1"]:
            nid = f"{self.prefix}{n}"
            try:
                self.cursor.execute(
                    "INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [nid])
            except Exception:
                pass
        edges = [
            ("A1", "MENTIONS", "E1"), ("A1", "MENTIONS", "E2"),
            ("A2", "MENTIONS", "E2"), ("A2", "MENTIONS", "E3"),
            ("A1", "CITES", "X1"),
            ("X1", "CITES", "A2"),
        ]
        for s, p, o in edges:
            try:
                self.cursor.execute(
                    "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                    [f"{self.prefix}{s}", p, f"{self.prefix}{o}"])
            except Exception:
                pass
        self.conn.commit()

    def test_out_mentions(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        result = ops.kg_NEIGHBORS(
            [f"{self.prefix}A1", f"{self.prefix}A2"], predicate="MENTIONS")
        assert f"{self.prefix}E1" in result
        assert f"{self.prefix}E2" in result
        assert f"{self.prefix}E3" in result

    def test_in_direction(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        result = ops.kg_NEIGHBORS(
            [f"{self.prefix}A2"], predicate="CITES", direction="in")
        assert f"{self.prefix}X1" in result

    def test_both_direction(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        result = ops.kg_NEIGHBORS(
            [f"{self.prefix}X1"], predicate="CITES", direction="both")
        assert f"{self.prefix}A1" in result or f"{self.prefix}A2" in result

    def test_no_predicate_returns_all(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        result = ops.kg_NEIGHBORS([f"{self.prefix}A1"], predicate=None)
        assert len(result) >= 3

    def test_mentions_alias(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        result = ops.kg_MENTIONS([f"{self.prefix}A1", f"{self.prefix}A2"])
        assert f"{self.prefix}E1" in result
        assert f"{self.prefix}E2" in result

    def test_empty_source(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        assert ops.kg_NEIGHBORS([]) == []


class TestVectorGraphSearchE2E:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        self.prefix = f"VGS_{uuid.uuid4().hex[:6]}_"
        self._insert_graph_with_embeddings()
        yield
        _cleanup(self.cursor, self.conn, self.prefix)

    def _insert_graph_with_embeddings(self):
        import numpy as np
        rng = np.random.default_rng(42)
        for suffix in ["A", "B", "C", "D", "E"]:
            nid = f"{self.prefix}{suffix}"
            self.cursor.execute(
                "INSERT INTO Graph_KG.nodes (node_id) SELECT ? WHERE NOT EXISTS "
                "(SELECT 1 FROM Graph_KG.nodes WHERE node_id = ?)", [nid, nid])
            if suffix in ("A", "B"):
                vec = rng.normal(0, 1, 768).tolist()
                vec_str = ",".join(f"{v:.6f}" for v in vec)
                self.cursor.execute(
                    "INSERT INTO Graph_KG.kg_NodeEmbeddings (id, emb) SELECT ?, TO_VECTOR(?, DOUBLE) "
                    "WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.kg_NodeEmbeddings WHERE id = ?)",
                    [nid, vec_str, nid])
        for s, o in [("A", "C"), ("A", "D"), ("B", "E")]:
            sid, oid = f"{self.prefix}{s}", f"{self.prefix}{o}"
            self.cursor.execute(
                "INSERT INTO Graph_KG.rdf_edges (s, p, o_id) SELECT ?, 'CONN', ? "
                "WHERE NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_edges WHERE s = ? AND o_id = ?)",
                [sid, oid, sid, oid])
        self.conn.commit()

    def test_returns_results(self):
        import numpy as np
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        query = json.dumps(np.random.default_rng(42).normal(0, 1, 768).tolist())
        results = ops.kg_VECTOR_GRAPH_SEARCH(
            query_vector=query, k_vector=2, k_final=10,
            expansion_depth=1, min_confidence=0.0)
        assert len(results) > 0


class TestInitializeSchemaIdempotent:

    def test_double_init_no_error(self, iris_connection):
        from iris_vector_graph.engine import IRISGraphEngine
        engine = IRISGraphEngine(iris_connection, embedding_dimension=768)
        engine.initialize_schema()
        engine.initialize_schema()
