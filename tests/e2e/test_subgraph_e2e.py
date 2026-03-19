"""E2E tests for kg_SUBGRAPH (023-kg-subgraph). Written FIRST per TDD."""
import json
import os
import time
import uuid

import pytest

from iris_vector_graph.models import SubgraphData

SKIP_IRIS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS, reason="SKIP_IRIS_TESTS=true")


def _insert_chain(cursor, conn, prefix):
    for n in ["A", "B", "C", "D", "E"]:
        nid = f"{prefix}{n}"
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [nid])
        except Exception:
            pass
    for s, p, o in [("A", "REL", "B"), ("B", "REL", "C"), ("C", "REL", "D"), ("A", "REL", "E")]:
        try:
            cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                           [f"{prefix}{s}", p, f"{prefix}{o}"])
        except Exception:
            pass
    for nid, key, val in [("A", "name", "NodeA"), ("B", "name", "NodeB"), ("C", "name", "NodeC")]:
        try:
            cursor.execute("INSERT INTO Graph_KG.rdf_props (s, key, val) VALUES (?, ?, ?)",
                           [f"{prefix}{nid}", key, val])
        except Exception:
            pass
    for nid, label in [("A", "Gene"), ("B", "Protein"), ("C", "Protein"), ("D", "Drug"), ("E", "Gene")]:
        try:
            cursor.execute("INSERT INTO Graph_KG.rdf_labels (s, label) VALUES (?, ?)",
                           [f"{prefix}{nid}", label])
        except Exception:
            pass
    conn.commit()


def _insert_mixed_edges(cursor, conn, prefix):
    for n in ["A", "B", "C", "D"]:
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [f"{prefix}{n}"])
        except Exception:
            pass
    for s, p, o in [("A", "MENTIONS", "B"), ("A", "CITES", "C"), ("B", "MENTIONS", "D")]:
        try:
            cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                           [f"{prefix}{s}", p, f"{prefix}{o}"])
        except Exception:
            pass
    conn.commit()


def _insert_cycle(cursor, conn, prefix):
    for n in ["A", "B"]:
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [f"{prefix}{n}"])
        except Exception:
            pass
    for s, o in [("A", "B"), ("B", "A")]:
        try:
            cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                           [f"{prefix}{s}", "REL", f"{prefix}{o}"])
        except Exception:
            pass
    conn.commit()


def _insert_hub(cursor, conn, prefix, n_spokes=100):
    hub = f"{prefix}HUB"
    try:
        cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [hub])
    except Exception:
        pass
    for i in range(n_spokes):
        spoke = f"{prefix}S{i}"
        try:
            cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [spoke])
        except Exception:
            pass
        try:
            cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                           [hub, "CONN", spoke])
        except Exception:
            pass
    conn.commit()


def _build_kg(conn):
    try:
        from iris_vector_graph.schema import _call_classmethod
        _call_classmethod(conn, 'Graph.KG.Traversal', 'BuildKG')
    except Exception as e:
        pytest.skip(f"BuildKG failed: {e}")


def _cleanup(cursor, conn, prefix):
    for table, col in [
        ("Graph_KG.kg_NodeEmbeddings", "id"),
        ("Graph_KG.rdf_props", "s"),
        ("Graph_KG.rdf_edges", "s"),
        ("Graph_KG.rdf_labels", "s"),
        ("Graph_KG.nodes", "node_id"),
    ]:
        try:
            cursor.execute(f"DELETE FROM {table} WHERE {col} LIKE ?", [f"{prefix}%"])
        except Exception:
            pass
    conn.commit()


class TestSubgraphChainGraph:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        self.prefix = f"SG_{uuid.uuid4().hex[:6]}_"
        _insert_chain(self.cursor, self.conn, self.prefix)
        _build_kg(self.conn)
        yield
        _cleanup(self.cursor, self.conn, self.prefix)

    def test_2hop_from_A(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        sg = ops.kg_SUBGRAPH(seed_ids=[f"{self.prefix}A"], k_hops=2)
        assert isinstance(sg, SubgraphData)
        assert f"{self.prefix}A" in sg.nodes
        assert f"{self.prefix}B" in sg.nodes
        assert f"{self.prefix}C" in sg.nodes
        assert f"{self.prefix}E" in sg.nodes
        assert f"{self.prefix}D" not in sg.nodes

    def test_1hop_from_A(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        sg = ops.kg_SUBGRAPH(seed_ids=[f"{self.prefix}A"], k_hops=1)
        assert f"{self.prefix}B" in sg.nodes
        assert f"{self.prefix}E" in sg.nodes
        assert f"{self.prefix}C" not in sg.nodes

    def test_multi_seed_union(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        sg = ops.kg_SUBGRAPH(seed_ids=[f"{self.prefix}A", f"{self.prefix}D"], k_hops=1)
        assert f"{self.prefix}A" in sg.nodes
        assert f"{self.prefix}B" in sg.nodes
        assert f"{self.prefix}D" in sg.nodes

    def test_nonexistent_seed_excluded(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        sg = ops.kg_SUBGRAPH(seed_ids=[f"{self.prefix}NOPE", f"{self.prefix}A"], k_hops=1)
        assert f"{self.prefix}A" in sg.nodes
        assert f"{self.prefix}NOPE" not in sg.nodes

    def test_k_hops_zero(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        sg = ops.kg_SUBGRAPH(seed_ids=[f"{self.prefix}A"], k_hops=0)
        assert sg.nodes == [f"{self.prefix}A"] or set(sg.nodes) == {f"{self.prefix}A"}
        assert sg.edges == []

    def test_properties_included(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        sg = ops.kg_SUBGRAPH(seed_ids=[f"{self.prefix}A"], k_hops=1)
        assert f"{self.prefix}A" in sg.node_properties
        assert sg.node_properties[f"{self.prefix}A"]["name"] == "NodeA"

    def test_labels_included(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        sg = ops.kg_SUBGRAPH(seed_ids=[f"{self.prefix}A"], k_hops=1)
        assert f"{self.prefix}A" in sg.node_labels
        assert "Gene" in sg.node_labels[f"{self.prefix}A"]

    def test_edges_are_triples(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        sg = ops.kg_SUBGRAPH(seed_ids=[f"{self.prefix}A"], k_hops=1)
        assert len(sg.edges) >= 2
        for edge in sg.edges:
            assert len(edge) == 3
            s, p, o = edge
            assert isinstance(s, str)
            assert isinstance(p, str)
            assert isinstance(o, str)

    def test_seed_ids_preserved(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        sg = ops.kg_SUBGRAPH(seed_ids=[f"{self.prefix}A"], k_hops=1)
        assert sg.seed_ids == [f"{self.prefix}A"]


class TestSubgraphEdgeTypeFilter:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        self.prefix = f"SGF_{uuid.uuid4().hex[:6]}_"
        _insert_mixed_edges(self.cursor, self.conn, self.prefix)
        _build_kg(self.conn)
        yield
        _cleanup(self.cursor, self.conn, self.prefix)

    def test_mentions_only(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        sg = ops.kg_SUBGRAPH(seed_ids=[f"{self.prefix}A"], k_hops=2, edge_types=["MENTIONS"])
        assert f"{self.prefix}B" in sg.nodes
        assert f"{self.prefix}D" in sg.nodes
        assert f"{self.prefix}C" not in sg.nodes
        for s, p, o in sg.edges:
            assert p == "MENTIONS"

    def test_none_includes_all(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        sg = ops.kg_SUBGRAPH(seed_ids=[f"{self.prefix}A"], k_hops=2, edge_types=None)
        assert f"{self.prefix}C" in sg.nodes


class TestSubgraphSafetyLimits:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        self.prefix = f"SGH_{uuid.uuid4().hex[:6]}_"
        _insert_hub(self.cursor, self.conn, self.prefix, n_spokes=100)
        _build_kg(self.conn)
        yield
        _cleanup(self.cursor, self.conn, self.prefix)

    def test_max_nodes_caps(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        sg = ops.kg_SUBGRAPH(seed_ids=[f"{self.prefix}HUB"], k_hops=1, max_nodes=10)
        assert len(sg.nodes) <= 10


class TestSubgraphCyclicGraph:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        self.prefix = f"SGC_{uuid.uuid4().hex[:6]}_"
        _insert_cycle(self.cursor, self.conn, self.prefix)
        _build_kg(self.conn)
        yield
        _cleanup(self.cursor, self.conn, self.prefix)

    def test_no_duplicates(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        sg = ops.kg_SUBGRAPH(seed_ids=[f"{self.prefix}A"], k_hops=5)
        assert len(sg.nodes) == len(set(sg.nodes))
        assert len(sg.edges) == len(set(sg.edges))


class TestSubgraphEmbeddings:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        self.prefix = f"SGE_{uuid.uuid4().hex[:6]}_"
        self._insert_data()
        _build_kg(self.conn)
        yield
        _cleanup(self.cursor, self.conn, self.prefix)

    def _insert_data(self):
        import numpy as np
        rng = np.random.default_rng(42)
        for n in ["A", "B", "C"]:
            nid = f"{self.prefix}{n}"
            try:
                self.cursor.execute("INSERT INTO Graph_KG.nodes (node_id) VALUES (?)", [nid])
            except Exception:
                pass
        for s, o in [("A", "B"), ("B", "C")]:
            try:
                self.cursor.execute("INSERT INTO Graph_KG.rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                                   [f"{self.prefix}{s}", "REL", f"{self.prefix}{o}"])
            except Exception:
                pass
        for n in ["A", "B"]:
            nid = f"{self.prefix}{n}"
            vec = rng.normal(0, 1, 768).tolist()
            vec_str = ",".join(f"{v:.6f}" for v in vec)
            try:
                self.cursor.execute(
                    "INSERT INTO Graph_KG.kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?, DOUBLE))",
                    [nid, vec_str])
            except Exception:
                pass
        self.conn.commit()

    def test_embeddings_included(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        sg = ops.kg_SUBGRAPH(seed_ids=[f"{self.prefix}A"], k_hops=2, include_embeddings=True)
        assert f"{self.prefix}A" in sg.node_embeddings
        assert f"{self.prefix}B" in sg.node_embeddings
        assert f"{self.prefix}C" not in sg.node_embeddings
        assert len(sg.node_embeddings[f"{self.prefix}A"]) == 768

    def test_embeddings_excluded_by_default(self):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(self.conn)
        sg = ops.kg_SUBGRAPH(seed_ids=[f"{self.prefix}A"], k_hops=2)
        assert sg.node_embeddings == {}


class TestSubgraphServerSide:

    @pytest.fixture(autouse=True)
    def setup(self, iris_connection):
        self.conn = iris_connection
        self.cursor = iris_connection.cursor()
        self.prefix = f"SGS_{uuid.uuid4().hex[:6]}_"
        _insert_chain(self.cursor, self.conn, self.prefix)
        _build_kg(self.conn)
        yield
        _cleanup(self.cursor, self.conn, self.prefix)

    def test_server_side_returns_valid_json(self):
        from iris_vector_graph.schema import _call_classmethod
        seed_json = json.dumps([f"{self.prefix}A"])
        try:
            result = _call_classmethod(self.conn, 'Graph.KG.Subgraph', 'SubgraphJson',
                                       seed_json, 2, '', 10000)
        except Exception as e:
            pytest.skip(f"SubgraphJson not deployed: {e}")
        parsed = json.loads(result)
        assert "nodes" in parsed
        assert "edges" in parsed
        assert f"{self.prefix}A" in parsed["nodes"]
        assert f"{self.prefix}B" in parsed["nodes"]


class TestSubgraphEmptyInput:

    def test_empty_seeds(self, iris_connection):
        from iris_vector_graph.operators import IRISGraphOperators
        ops = IRISGraphOperators(iris_connection)
        sg = ops.kg_SUBGRAPH(seed_ids=[])
        assert sg.nodes == []
        assert sg.edges == []
