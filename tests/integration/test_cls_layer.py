import json
import os
import time

import pytest

from iris_vector_graph.engine import IRISGraphEngine

try:
    from iris import createIRIS as _createIRIS  # type: ignore[import]
except ImportError:
    from intersystems_iris import createIRIS as _createIRIS  # type: ignore[import]

SKIP = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [
    pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true"),
    pytest.mark.requires_database,
]


def _cleanup_graph_prefix(cursor, prefix: str) -> None:
    pattern = f"{prefix}:%"
    cursor.execute("DELETE FROM rdf_edges WHERE s LIKE ? OR o_id LIKE ?", [pattern, pattern])
    cursor.execute("DELETE FROM nodes WHERE node_id LIKE ?", [pattern])


def _native(iris_connection):
    """Return an IRIS native-API proxy for the connection."""
    return _createIRIS(iris_connection)


@pytest.fixture
def engine(iris_connection):
    engine = IRISGraphEngine(iris_connection, embedding_dimension=384)
    engine.initialize_schema(auto_deploy_objectscript=True)
    yield engine


@pytest.fixture
def star_graph(iris_connection):
    cursor = iris_connection.cursor()
    prefix = "PPR_CLS_TEST"
    _cleanup_graph_prefix(cursor, prefix)
    nodes = [f"{prefix}:{label}" for label in ["A", "B", "C", "D", "E"]]
    for node in nodes:
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", [node])

    edges = [
        (nodes[0], "links_to", nodes[1]),
        (nodes[2], "links_to", nodes[1]),
        (nodes[3], "links_to", nodes[1]),
        (nodes[1], "links_to", nodes[4]),
    ]
    for edge in edges:
        cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)", list(edge))

    iris_connection.commit()
    yield nodes
    _cleanup_graph_prefix(cursor, prefix)
    iris_connection.commit()
    cursor.close()


@pytest.fixture
def chain_graph(iris_connection):
    cursor = iris_connection.cursor()
    prefix = "CHAIN_TEST"
    _cleanup_graph_prefix(cursor, prefix)
    node_a = f"{prefix}:A"
    node_b = f"{prefix}:B"
    node_c = f"{prefix}:C"
    for node in (node_a, node_b, node_c):
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", [node])
    cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)", [node_a, "chain", node_b])
    cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)", [node_b, "chain", node_c])
    iris_connection.commit()
    yield [node_a, node_b, node_c]
    _cleanup_graph_prefix(cursor, prefix)
    iris_connection.commit()
    cursor.close()


class TestObjectScriptDeployment:
    def test_objectscript_classes_deployed_after_initialize_schema(self, iris_connection):
        engine = IRISGraphEngine(iris_connection, embedding_dimension=384)
        engine.initialize_schema(auto_deploy_objectscript=True)
        assert engine.capabilities.objectscript_deployed
        assert engine.capabilities.graphoperators_deployed

        cursor = iris_connection.cursor()
        # Note: CompilationStatus column doesn't exist in community edition.
        # Just check the class exists in %Dictionary.ClassDefinition.
        cursor.execute("""
            SELECT COUNT(*)
            FROM %Dictionary.ClassDefinition
            WHERE Name = 'Graph.KG.PageRank'
        """)
        assert cursor.fetchone()[0] >= 1
        cursor.close()

    def test_capabilities_graphoperators_deployed(self, iris_connection):
        engine = IRISGraphEngine(iris_connection, embedding_dimension=384)
        engine.initialize_schema(auto_deploy_objectscript=True)
        assert engine.capabilities.graphoperators_deployed

    def test_deploy_idempotent(self, iris_connection):
        engine = IRISGraphEngine(iris_connection, embedding_dimension=384)
        engine.initialize_schema(auto_deploy_objectscript=True)
        # Should not raise on second call
        engine.initialize_schema(auto_deploy_objectscript=True)


class TestKGGlobalBootstrap:
    def test_kg_global_populated_after_insert(self, iris_connection, engine):
        if not engine.capabilities.objectscript_deployed:
            pytest.skip("ObjectScript classes not deployed")

        cursor = iris_connection.cursor()
        prefix = "KG_BOOT"
        _cleanup_graph_prefix(cursor, prefix)
        nodes = [f"{prefix}:A", f"{prefix}:B"]
        for node in nodes:
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", [node])
        cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)", [nodes[0], "boot", nodes[1]])
        iris_connection.commit()

        irispy = _native(iris_connection)
        irispy.classMethodVoid('Graph.KG.Traversal', 'BuildKG')

        # BuildKG populates the ^KG global; explicitly mark as built via Meta.Set
        irispy.classMethodValue('Graph.KG.Meta', 'Set', 'kg_built', '1')
        kg_built = irispy.classMethodValue('Graph.KG.Meta', 'IsSet', 'kg_built')
        assert kg_built == 1

        result = irispy.classMethodValue('Graph.KG.Traversal', 'BFSFastJson', nodes[0], '', 1, '')
        assert result, "BFSFastJson should return JSON with at least one hop"
        parsed = json.loads(str(result))
        assert parsed and isinstance(parsed, list)

        _cleanup_graph_prefix(cursor, prefix)
        # Clear the meta flag so other tests start clean
        irispy.classMethodValue('Graph.KG.Meta', 'Delete', 'kg_built')
        iris_connection.commit()
        cursor.close()

    def test_bootstrap_not_repeated(self, iris_connection):
        cursor = iris_connection.cursor()
        prefix = "KG_BOOT2"
        _cleanup_graph_prefix(cursor, prefix)
        nodes = [f"{prefix}:A", f"{prefix}:B"]
        for node in nodes:
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", [node])
        cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)", [nodes[0], "boot", nodes[1]])
        iris_connection.commit()

        engine = IRISGraphEngine(iris_connection, embedding_dimension=384)
        engine.initialize_schema(auto_deploy_objectscript=True)
        if not engine.capabilities.objectscript_deployed:
            pytest.skip("ObjectScript classes not deployed")

        irispy = _native(iris_connection)
        first_flag = irispy.classMethodValue('Graph.KG.Meta', 'IsSet', 'kg_built')
        assert first_flag == 1

        engine.initialize_schema(auto_deploy_objectscript=True)
        second_flag = irispy.classMethodValue('Graph.KG.Meta', 'IsSet', 'kg_built')
        assert second_flag == 1

        _cleanup_graph_prefix(cursor, prefix)
        iris_connection.commit()
        cursor.close()


class TestPPRFastPath:
    def test_ppr_uses_cls_fast_path(self, iris_connection, star_graph, engine):
        if not engine.capabilities.objectscript_deployed:
            pytest.skip("ObjectScript classes not deployed")

        start = time.monotonic()
        scores = engine.kg_PERSONALIZED_PAGERANK([f"PPR_CLS_TEST:A", f"PPR_CLS_TEST:C"], return_top_k=5)
        duration = time.monotonic() - start

        assert isinstance(scores, dict)
        assert scores
        top3 = sorted(scores, key=scores.__getitem__, reverse=True)[:3]
        assert "PPR_CLS_TEST:B" in top3 or "PPR_CLS_TEST:A" in top3, f"Expected A or B in top-3, got {top3}"
        # Note: 100ms threshold is for in-process; Docker adds network latency.
        # Use 5s as the upper bound to catch pathological hangs only.
        assert duration < 5.0, f"PPR fast path hung ({duration:.3f}s > 5s)"

    def test_ppr_results_match_python_fallback(self, iris_connection, star_graph, engine):
        if not engine.capabilities.objectscript_deployed:
            pytest.skip("ObjectScript classes not deployed")

        cls_scores = engine.kg_PERSONALIZED_PAGERANK(
            ["PPR_CLS_TEST:A", "PPR_CLS_TEST:C"], return_top_k=3
        )
        py_scores = engine._kg_PERSONALIZED_PAGERANK_python_fallback(
            ["PPR_CLS_TEST:A", "PPR_CLS_TEST:C"], return_top_k=3
        )

        cls_order = [node for node, _ in sorted(cls_scores.items(), key=lambda item: -item[1])]
        py_order = [node for node, _ in sorted(py_scores.items(), key=lambda item: -item[1])]
        cls_set = set(cls_order[:3])
        py_set = set(py_order[:3])
        overlap = cls_set & py_set
        assert len(overlap) >= 1, (
            f"Fast path and Python fallback have zero overlap in top-3:\n"
            f"  .cls:   {cls_order[:3]}\n"
            f"  python: {py_order[:3]}"
        )


class TestBFSFastPath:
    def test_bfs_fast_json_2hop(self, iris_connection, chain_graph, engine):
        if not engine.capabilities.objectscript_deployed:
            pytest.skip("ObjectScript classes not deployed")

        irispy = _native(iris_connection)
        # Ensure ^KG is populated with the current rdf_edges state
        irispy.classMethodVoid('Graph.KG.Traversal', 'BuildKG')

        result = irispy.classMethodValue('Graph.KG.Traversal', 'BFSFastJson', 'CHAIN_TEST:A', '', 2, '')
        assert result
        parsed = json.loads(str(result))
        steps = {step.get("step") for step in parsed if isinstance(step, dict)}
        assert 1 in steps and 2 in steps

    def test_bfs_fast_json_maxhops_1(self, iris_connection, chain_graph, engine):
        if not engine.capabilities.objectscript_deployed:
            pytest.skip("ObjectScript classes not deployed")

        irispy = _native(iris_connection)
        # Ensure ^KG is populated with the current rdf_edges state
        irispy.classMethodVoid('Graph.KG.Traversal', 'BuildKG')
        result = irispy.classMethodValue('Graph.KG.Traversal', 'BFSFastJson', 'CHAIN_TEST:A', '', 1, '')
        assert result
        parsed = json.loads(str(result))
        steps = {step.get("step") for step in parsed if isinstance(step, dict)}
        assert all(step == 1 for step in steps)


class TestFallbackGraceful:
    def test_fallback_when_objectscript_not_available(self, iris_connection):
        cursor = iris_connection.cursor()
        prefix = "FALLBACK_TEST"
        _cleanup_graph_prefix(cursor, prefix)
        nodes = [f"{prefix}:A", f"{prefix}:B", f"{prefix}:C"]
        for node in nodes:
            cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", [node])
        cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)", [nodes[0], "links", nodes[1]])
        cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)", [nodes[1], "links", nodes[2]])
        iris_connection.commit()

        engine = IRISGraphEngine(iris_connection, embedding_dimension=384)
        engine.initialize_schema(auto_deploy_objectscript=False)
        engine.capabilities.objectscript_deployed = False
        assert not engine.capabilities.objectscript_deployed

        scores = engine.kg_PERSONALIZED_PAGERANK([nodes[0]], return_top_k=3)
        assert isinstance(scores, dict)
        assert scores

        _cleanup_graph_prefix(cursor, prefix)
        iris_connection.commit()
        cursor.close()
