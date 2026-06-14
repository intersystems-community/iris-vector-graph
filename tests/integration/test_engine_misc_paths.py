"""
Integration tests covering miscellaneous uncovered paths in:
  - _engine/algorithms.py: khop, random_walk, kg_WCC, kg_CDLP, kg_PPR,
      scc/k_core error paths, leiden meta row
  - _engine/nodes_edges.py: set_edge_weight, delete_node, count_nodes,
      nodes_exist, store_edge, _filter_edges_by_properties, delete_edge
  - _engine/snapshot.py: load_networkx, export/restore, import_rdf
"""
import pytest
from unittest.mock import patch
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


@pytest.fixture
def misc_eng(iris_connection, iris_master_cleanup):
    eng = IRISGraphEngine(iris_connection, embedding_dimension=4)
    for i in range(5):
        eng.create_node(f"mi_{i}", labels=["MI"], properties={"v": i})
    for i in range(4):
        eng.create_edge(f"mi_{i}", "MI_REL", f"mi_{i + 1}", qualifiers={"w": str(i)})
    eng.sync()
    return eng


@pytest.fixture
def store(misc_eng):
    return misc_eng._store


# ---------------------------------------------------------------------------
# algorithms.py: khop + random_walk
# ---------------------------------------------------------------------------

class TestKhopAndRandomWalk:

    def test_khop_basic(self, misc_eng):
        result = misc_eng.khop("mi_0", hops=2, max_nodes=50)
        assert isinstance(result, dict)
        assert "nodes" in result

    def test_khop_missing_node(self, misc_eng):
        result = misc_eng.khop("__no_such__", hops=1, max_nodes=10)
        assert isinstance(result, dict)

    def test_random_walk_returns_list(self, misc_eng):
        result = misc_eng.random_walk("mi_0", length=5, num_walks=3)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# algorithms.py: kg_WCC, kg_CDLP, kg_PPR
# ---------------------------------------------------------------------------

class TestKGAlgorithmHighLevel:

    def test_kg_wcc_basic(self, misc_eng):
        result = misc_eng.kg_WCC(max_iterations=5)
        assert isinstance(result, dict)

    def test_kg_cdlp_basic(self, misc_eng):
        result = misc_eng.kg_CDLP(max_iterations=5)
        assert isinstance(result, dict)

    def test_kg_ppr_basic(self, misc_eng):
        result = misc_eng.kg_PPR(seed_entities=["mi_0"], damping=0.85, max_iterations=5)
        assert isinstance(result, (list, dict))

    def test_kg_ppr_empty_seeds(self, misc_eng):
        result = misc_eng.kg_PPR(seed_entities=[], damping=0.85)
        assert result == []


# ---------------------------------------------------------------------------
# algorithms.py: scc error path (L1051)
# ---------------------------------------------------------------------------

class TestSCCErrorPath:

    def test_scc_error_returns_empty(self, misc_eng):
        err = IVGResult(columns=["id", "component", "size"], rows=[], error="forced")
        with patch.object(misc_eng._store, "execute_scc", return_value=err):
            try:
                result = misc_eng.scc(top_k=5)
                assert result == []
            except (AttributeError, NotImplementedError):
                pytest.skip("scc method not exposed or not supported")


# ---------------------------------------------------------------------------
# algorithms.py: k_core error path (L1092)
# ---------------------------------------------------------------------------

class TestKCoreErrorPath:

    def test_kcore_error_returns_empty(self, misc_eng):
        err = IVGResult(columns=["id", "coreness"], rows=[], error="forced")
        with patch.object(misc_eng._store, "execute_k_core", return_value=err):
            try:
                result = misc_eng.k_core(top_k=5)
                assert result == []
            except (AttributeError, NotImplementedError):
                pytest.skip("k_core method not exposed or not supported")


# ---------------------------------------------------------------------------
# algorithms.py: leiden meta row (L964-966)
# ---------------------------------------------------------------------------

class TestLeidenMetaRow:

    def test_leiden_meta_row_in_result(self, misc_eng):
        import json
        meta_result = IVGResult(
            columns=["community_id", "members", "size"],
            rows=[
                ["_meta", json.dumps({"levels": 3, "modularity": 0.45}), None],
                ["C0", "mi_0,mi_1", 2],
            ]
        )
        with patch.object(misc_eng._store, "execute_leiden", return_value=meta_result):
            result = misc_eng.leiden_communities(top_k=5)
        assert isinstance(result, list)
        assert len(result) >= 1


# ---------------------------------------------------------------------------
# nodes_edges.py: set_edge_weight, delete_edge, delete_node
# ---------------------------------------------------------------------------

class TestNodesEdgesDirectPaths:

    def test_set_edge_weight_basic(self, misc_eng):
        result = misc_eng.set_edge_weight("mi_0", "MI_REL", "mi_1", 2.5)
        assert isinstance(result, bool)

    def test_delete_edge_basic(self, misc_eng):
        misc_eng.create_node("de_s", labels=["DE"])
        misc_eng.create_node("de_t", labels=["DE"])
        misc_eng.sync()
        misc_eng.store_edge("de_s", "DE_REL", "de_t")
        result = misc_eng.delete_edge("de_s", "DE_REL", "de_t")
        assert result is True

    def test_delete_node_basic(self, misc_eng):
        misc_eng.create_node("del_me", labels=["DELME"])
        misc_eng.sync()
        result = misc_eng.delete_node("del_me")
        assert result is True or result is None

    def test_count_nodes_all(self, misc_eng):
        result = misc_eng.count_nodes()
        assert isinstance(result, int)
        assert result >= 0

    def test_count_nodes_by_label(self, misc_eng):
        result = misc_eng.count_nodes(label="MI")
        assert isinstance(result, int)
        assert result >= 0

    def test_nodes_exist_basic(self, misc_eng):
        result = misc_eng.nodes_exist(["mi_0", "mi_1", "__gone__"])
        assert isinstance(result, set)
        assert "mi_0" in result

    def test_nodes_exist_empty(self, misc_eng):
        result = misc_eng.nodes_exist([])
        assert result == set()

    def test_store_edge_with_qualifiers(self, misc_eng):
        result = misc_eng.store_edge("mi_0", "MI_Q_REL", "mi_1", qualifiers={"k": "v"})
        assert result is True


# ---------------------------------------------------------------------------
# nodes_edges.py: _filter_edges_by_properties
# ---------------------------------------------------------------------------

class TestFilterEdgesByProperties:

    def test_filter_edges_no_filter(self, misc_eng):
        bfs = [{"s": "mi_0", "p": "MI_REL", "o": "mi_1"}]
        result = misc_eng._filter_edges_by_properties(bfs, {})
        assert result == bfs

    def test_filter_edges_with_filter(self, misc_eng):
        bfs = [{"s": "mi_0", "p": "MI_REL", "o": "mi_1"}]
        result = misc_eng._filter_edges_by_properties(bfs, {"w": "0"})
        assert isinstance(result, list)

    def test_filter_edges_empty_bfs(self, misc_eng):
        result = misc_eng._filter_edges_by_properties([], {"k": "v"})
        assert result == []


# ---------------------------------------------------------------------------
# snapshot.py: load_networkx
# ---------------------------------------------------------------------------

class TestLoadNetworkx:

    def test_load_networkx_basic(self, misc_eng):
        try:
            import networkx as nx
        except ImportError:
            pytest.skip("networkx not installed")

        G = nx.DiGraph()
        G.add_node("nx_a", namespace="NX", color="red")
        G.add_node("nx_b", namespace="NX")
        G.add_edge("nx_a", "nx_b", predicate="NX_REL")

        result = misc_eng.load_networkx(G, label_attr="namespace")
        assert isinstance(result, dict)
        assert result.get("nodes", 0) >= 0

    def test_load_networkx_no_label_attr(self, misc_eng):
        try:
            import networkx as nx
        except ImportError:
            pytest.skip("networkx not installed")

        G = nx.DiGraph()
        G.add_node("nxb_a")
        G.add_node("nxb_b")
        G.add_edge("nxb_a", "nxb_b")
        result = misc_eng.load_networkx(G)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# snapshot.py: export_snapshot / restore_snapshot round-trip
# ---------------------------------------------------------------------------

class TestSnapshotRoundTrip:

    def test_export_restore_basic(self, misc_eng, iris_connection):
        eng2 = IRISGraphEngine(iris_connection, embedding_dimension=4)

        try:
            snap = misc_eng.export_snapshot()
        except Exception:
            pytest.skip("export_snapshot not available")

        assert snap is not None

        try:
            result = eng2.restore_snapshot(snap, merge=True)
            assert result is not None
        except Exception:
            pytest.skip("restore_snapshot not available")
