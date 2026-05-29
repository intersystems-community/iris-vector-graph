"""E2E tests for LazyKG against live ivg-iris ^KG (Spec 163 T022c)."""

import os
import uuid

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.stores.lazy_kg import LazyKG


SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


def _build_three_nodes(engine, prefix):
    engine.create_node(prefix + "alice")
    engine.create_node(prefix + "bob")
    engine.create_node(prefix + "carol")
    engine.create_edge(prefix + "alice", "KNOWS", prefix + "bob")
    engine.create_edge(prefix + "alice", "KNOWS", prefix + "carol")
    engine.create_edge(prefix + "bob", "KNOWS", prefix + "carol")
    engine.conn.commit()
    from iris_vector_graph.schema import _call_classmethod
    _call_classmethod(engine.conn, "Graph.KG.Traversal", "BuildKG")


class TestLazyKGAgainstLiveIRIS:
    def test_iter_nodes_returns_all_three(self, iris_connection, iris_master_cleanup):
        engine = IRISGraphEngine(iris_connection)
        prefix = f"lkg_{uuid.uuid4().hex[:8]}_"
        _build_three_nodes(engine, prefix)

        lkg = LazyKG(iris_connection)
        all_nodes = [n for n in lkg.iter_nodes() if n.startswith(prefix)]
        assert set(all_nodes) == {prefix + "alice", prefix + "bob", prefix + "carol"}

    def test_out_neighbors_returns_actual_targets(self, iris_connection, iris_master_cleanup):
        engine = IRISGraphEngine(iris_connection)
        prefix = f"lkg_{uuid.uuid4().hex[:8]}_"
        _build_three_nodes(engine, prefix)

        lkg = LazyKG(iris_connection)
        alice_targets = set(lkg.out_neighbors(prefix + "alice"))
        assert alice_targets == {prefix + "bob", prefix + "carol"}

        carol_targets = set(lkg.out_neighbors(prefix + "carol"))
        assert carol_targets == set()

    def test_in_neighbors_returns_actual_sources(self, iris_connection, iris_master_cleanup):
        engine = IRISGraphEngine(iris_connection)
        prefix = f"lkg_{uuid.uuid4().hex[:8]}_"
        _build_three_nodes(engine, prefix)

        lkg = LazyKG(iris_connection)
        carol_sources = set(lkg.in_neighbors(prefix + "carol"))
        assert carol_sources == {prefix + "alice", prefix + "bob"}

        alice_sources = set(lkg.in_neighbors(prefix + "alice"))
        assert alice_sources == set()

    def test_degree_matches_kg_deg(self, iris_connection, iris_master_cleanup):
        engine = IRISGraphEngine(iris_connection)
        prefix = f"lkg_{uuid.uuid4().hex[:8]}_"
        _build_three_nodes(engine, prefix)

        lkg = LazyKG(iris_connection)
        assert lkg.degree(prefix + "alice") == 2
        assert lkg.degree(prefix + "bob") == 1
        assert lkg.degree(prefix + "carol") == 0

    def test_caching_avoids_repeat_iris_calls(self, iris_connection, iris_master_cleanup):
        engine = IRISGraphEngine(iris_connection)
        prefix = f"lkg_{uuid.uuid4().hex[:8]}_"
        _build_three_nodes(engine, prefix)

        lkg = LazyKG(iris_connection)
        first = lkg.out_neighbors(prefix + "alice")
        second = lkg.out_neighbors(prefix + "alice")
        assert first == second
        assert lkg.cache_stats()["out_cached_nodes"] == 1
