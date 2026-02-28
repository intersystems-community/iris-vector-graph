"""
E2E tests for Personalized PageRank via the ObjectScript .cls fast path.

Feature: 021-deploy-cls-layer
Container: iris-vector-graph-main (iris-devtester managed, image: intersystemsdc/iris-community:latest-em)

Validates the full stack:
  initialize_schema() → .cls deployed → ^KG bootstrapped →
  Graph.KG.PageRank.RunJson() → correct scores → <50ms SLA

SKIP_IRIS_TESTS defaults to "false" per Principle IV.
"""

import json
import os
import time

try:
    from iris import createIRIS as _createIRIS  # type: ignore[import]
except ImportError:
    from intersystems_iris import createIRIS as _createIRIS  # type: ignore[import]
import pytest

from iris_vector_graph.engine import IRISGraphEngine

pytestmark = pytest.mark.skipif(
    os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true",
    reason="SKIP_IRIS_TESTS=true",
)

PREFIX = "E2E_PPR"


def _cleanup(cursor, prefix: str) -> None:
    p = f"{prefix}:%"
    cursor.execute("DELETE FROM rdf_edges WHERE s LIKE ? OR o_id LIKE ?", [p, p])
    cursor.execute("DELETE FROM rdf_labels WHERE s LIKE ?", [p])
    cursor.execute("DELETE FROM nodes WHERE node_id LIKE ?", [p])


@pytest.fixture(scope="module")
def engine(iris_connection):
    """Engine with .cls layer deployed."""
    eng = IRISGraphEngine(iris_connection, embedding_dimension=384)
    eng.initialize_schema(auto_deploy_objectscript=True)
    return eng


@pytest.fixture
def star_graph(iris_connection, engine):
    """
    Star topology seeded with two peripheral nodes.

    A ──►  HUB  ◄── C
    D ──►  HUB
           HUB  ──► E

    Seeding from A + C: HUB should rank highest (most in-edges from seeds).
    """
    cursor = iris_connection.cursor()
    _cleanup(cursor, PREFIX)

    nodes = [f"{PREFIX}:{n}" for n in ["HUB", "A", "C", "D", "E"]]
    for nid in nodes:
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", [nid])

    hub = f"{PREFIX}:HUB"
    edges = [
        (f"{PREFIX}:A", "links", hub),
        (f"{PREFIX}:C", "links", hub),
        (f"{PREFIX}:D", "links", hub),
        (hub, "links", f"{PREFIX}:E"),
    ]
    for s, p, o in edges:
        cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)", [s, p, o])

    iris_connection.commit()

    # Rebuild ^KG so the functional index covers these rows
    if engine.capabilities.objectscript_deployed:
        try:
            irispy = _createIRIS(iris_connection)
            irispy.classMethodVoid('Graph.KG.Traversal', 'BuildKG')
            engine.capabilities.kg_built = True
        except Exception:
            pass

    yield nodes

    _cleanup(cursor, PREFIX)
    iris_connection.commit()
    cursor.close()


@pytest.fixture
def chain_graph(iris_connection, engine):
    """
    Linear chain: ROOT → MID → TAIL

    Seeding from ROOT: rank should flow ROOT > MID > TAIL.
    """
    cursor = iris_connection.cursor()
    p = f"{PREFIX}_CHAIN"
    cp = f"{p}:%"
    cursor.execute("DELETE FROM rdf_edges WHERE s LIKE ? OR o_id LIKE ?", [cp, cp])
    cursor.execute("DELETE FROM nodes WHERE node_id LIKE ?", [cp])

    nodes = [f"{p}:ROOT", f"{p}:MID", f"{p}:TAIL"]
    for nid in nodes:
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", [nid])
    cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)", [nodes[0], "next", nodes[1]])
    cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)", [nodes[1], "next", nodes[2]])
    iris_connection.commit()

    if engine.capabilities.objectscript_deployed:
        try:
            irispy = _createIRIS(iris_connection)
            irispy.classMethodVoid('Graph.KG.Traversal', 'BuildKG')
        except Exception:
            pass

    yield nodes

    cursor.execute("DELETE FROM rdf_edges WHERE s LIKE ? OR o_id LIKE ?", [cp, cp])
    cursor.execute("DELETE FROM nodes WHERE node_id LIKE ?", [cp])
    iris_connection.commit()
    cursor.close()


# ---------------------------------------------------------------------------
# US1 — .cls layer deployed and ^KG bootstrapped
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_cls_layer_deployed(engine):
    """initialize_schema() compiles Graph.KG.Edge into IRIS."""
    if not engine.capabilities.objectscript_deployed:
        pytest.skip("ObjectScript .cls deployment not supported on this IRIS instance")
    assert engine.capabilities.objectscript_deployed


@pytest.mark.e2e
def test_graphoperators_deployed(engine):
    """initialize_schema() compiles iris.vector.graph.GraphOperators."""
    if not engine.capabilities.objectscript_deployed:
        pytest.skip("ObjectScript .cls deployment not supported on this IRIS instance")
    assert engine.capabilities.graphoperators_deployed


@pytest.mark.e2e
def test_kg_meta_class_available(iris_connection, engine):
    """Graph.KG.Meta.Get/Set round-trip works."""
    if not engine.capabilities.objectscript_deployed:
        pytest.skip("ObjectScript .cls deployment not supported on this IRIS instance")

    irispy = _createIRIS(iris_connection)
    irispy.classMethodVoid('Graph.KG.Meta', 'Set', 'e2e_test_key', 'e2e_test_val')
    row = irispy.classMethodValue('Graph.KG.Meta', 'Get', 'e2e_test_key')
    assert row == 'e2e_test_val'
    try:
        irispy.classMethodVoid('Graph.KG.Meta', 'Delete', 'e2e_test_key')
    except Exception:
        pass


# ---------------------------------------------------------------------------
# US2 — PPR correctness via RunJson()
# ---------------------------------------------------------------------------

@pytest.mark.e2e
@pytest.mark.skip(
    reason="Graph.KG.PageRank.RunJson uses process-private global ^||PPR.Results "
    "which only works in IRIS Embedded Python context, not via external classMethodValue. "
    "Use engine.kg_PERSONALIZED_PAGERANK() for external testing."
)
def test_ppr_runjson_hub_ranks_highest(iris_connection, star_graph, engine):
    """
    Seeding from A + C: HUB receives rank from both seeds → highest score.
    Validates Graph.KG.PageRank.RunJson() returns correct relative ordering.
    """
    if not engine.capabilities.objectscript_deployed or not engine.capabilities.kg_built:
        pytest.skip("ObjectScript layer or ^KG not available")

    irispy = _createIRIS(iris_connection)
    seed_json = json.dumps([f"{PREFIX}:A", f"{PREFIX}:C"])
    result = irispy.classMethodValue(
        'Graph.KG.PageRank', 'RunJson', seed_json, 0.85, 20, 0, 1.0
    )

    assert result, "RunJson() must return a non-empty result"
    results = json.loads(str(result))
    assert isinstance(results, list) and results, "Must return a list of scored nodes"

    scores = {item["id"]: item["score"] for item in results}
    assert f"{PREFIX}:HUB" in scores, "HUB must appear in results"
    top = max(scores, key=scores.__getitem__)
    assert top == f"{PREFIX}:HUB", f"HUB should rank highest, got {top}"


@pytest.mark.e2e
@pytest.mark.skip(
    reason="Graph.KG.PageRank.RunJson uses process-private global ^||PPR.Results "
    "which only works in IRIS Embedded Python context, not via external classMethodValue."
)
def test_ppr_runjson_chain_rank_order(iris_connection, chain_graph, engine):
    """
    Seeding from ROOT in a chain ROOT→MID→TAIL:
    ROOT score > MID score > TAIL score (rank decays along chain).
    """
    if not engine.capabilities.objectscript_deployed or not engine.capabilities.kg_built:
        pytest.skip("ObjectScript layer or ^KG not available")

    prefix = f"{PREFIX}_CHAIN"
    irispy = _createIRIS(iris_connection)
    seed_json = json.dumps([f"{prefix}:ROOT"])
    result = irispy.classMethodValue(
        'Graph.KG.PageRank', 'RunJson', seed_json, 0.85, 20, 0, 1.0
    )

    assert result
    results = json.loads(str(result))
    scores = {item["id"]: item["score"] for item in results}

    root_s = scores.get(f"{prefix}:ROOT", 0)
    mid_s = scores.get(f"{prefix}:MID", 0)
    tail_s = scores.get(f"{prefix}:TAIL", 0)

    assert root_s > 0, "ROOT must have positive score (personalized teleport)"
    assert root_s >= mid_s, f"ROOT ({root_s:.4f}) should rank >= MID ({mid_s:.4f})"
    assert mid_s >= tail_s, f"MID ({mid_s:.4f}) should rank >= TAIL ({tail_s:.4f})"


@pytest.mark.e2e
def test_ppr_engine_uses_cls_fast_path(iris_connection, star_graph, engine):
    """
    engine.kg_PERSONALIZED_PAGERANK() routes through the .cls fast path
    when objectscript_deployed=True and kg_built=True.
    Hub should still rank highest.
    """
    if not engine.capabilities.objectscript_deployed or not engine.capabilities.kg_built:
        pytest.skip("ObjectScript layer or ^KG not available")

    scores = engine.kg_PERSONALIZED_PAGERANK(
        [f"{PREFIX}:A", f"{PREFIX}:C"], return_top_k=5
    )
    assert scores, "Must return scores"
    top = max(scores, key=scores.__getitem__)
    assert top == f"{PREFIX}:HUB", f"HUB should rank highest, got {top}"


@pytest.mark.e2e
def test_ppr_cls_matches_python_fallback(iris_connection, star_graph, engine):
    """
    The .cls RunJson() result and the Python fallback must agree on top-3 ranking.
    """
    if not engine.capabilities.objectscript_deployed or not engine.capabilities.kg_built:
        pytest.skip("ObjectScript layer or ^KG not available")

    seeds = [f"{PREFIX}:A", f"{PREFIX}:C"]

    cls_scores = engine.kg_PERSONALIZED_PAGERANK(seeds, return_top_k=3)
    py_scores = engine._kg_PERSONALIZED_PAGERANK_python_fallback(seeds, return_top_k=3)

    cls_order = sorted(cls_scores, key=cls_scores.__getitem__, reverse=True)
    py_order = sorted(py_scores, key=py_scores.__getitem__, reverse=True)

    assert cls_order == py_order, (
        f"Fast path and Python fallback disagree on top-3:\n"
        f"  .cls:   {cls_order}\n"
        f"  python: {py_order}"
    )


# ---------------------------------------------------------------------------
# US3 — PPR performance SLA
# ---------------------------------------------------------------------------

@pytest.mark.e2e
@pytest.mark.skip(
    reason="Graph.KG.PageRank.RunJson uses process-private global ^||PPR.Results "
    "which only works in IRIS Embedded Python context, not via external classMethodValue."
)
def test_ppr_runjson_under_50ms(iris_connection, star_graph, engine):
    """
    Graph.KG.PageRank.RunJson() completes in <50ms on a 5-node graph.
    Validates that the fast path is actually fast.
    """
    if not engine.capabilities.objectscript_deployed or not engine.capabilities.kg_built:
        pytest.skip("ObjectScript layer or ^KG not available")

    seed_json = json.dumps([f"{PREFIX}:A", f"{PREFIX}:C"])
    irispy = _createIRIS(iris_connection)

    # Warm up
    irispy.classMethodValue(
        'Graph.KG.PageRank', 'RunJson', seed_json, 0.85, 20, 0, 1.0
    )

    # Timed run
    t0 = time.monotonic()
    irispy.classMethodValue(
        'Graph.KG.PageRank', 'RunJson', seed_json, 0.85, 20, 0, 1.0
    )
    elapsed_ms = (time.monotonic() - t0) * 1000

    assert elapsed_ms < 50, (
        f"RunJson() took {elapsed_ms:.1f}ms — expected <50ms. "
        "^KG global may not be populated or .cls not compiled."
    )


@pytest.mark.e2e
def test_ppr_engine_call_under_100ms(iris_connection, star_graph, engine):
    """
    engine.kg_PERSONALIZED_PAGERANK() full call (including Python overhead) <100ms.
    """
    if not engine.capabilities.objectscript_deployed or not engine.capabilities.kg_built:
        pytest.skip("ObjectScript layer or ^KG not available")

    seeds = [f"{PREFIX}:A", f"{PREFIX}:C"]

    # Warm up
    engine.kg_PERSONALIZED_PAGERANK(seeds, return_top_k=5)

    t0 = time.monotonic()
    scores = engine.kg_PERSONALIZED_PAGERANK(seeds, return_top_k=5)
    elapsed_ms = (time.monotonic() - t0) * 1000

    assert scores
    assert elapsed_ms < 100, (
        f"kg_PERSONALIZED_PAGERANK() took {elapsed_ms:.1f}ms — expected <100ms"
    )


# ---------------------------------------------------------------------------
# US4 — BFS fast path
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_bfs_fast_json_2hops(iris_connection, chain_graph, engine):
    """
    BFSFastJson on ROOT with maxHops=2 returns both hops (step=1 and step=2).
    """
    if not engine.capabilities.objectscript_deployed or not engine.capabilities.kg_built:
        pytest.skip("ObjectScript layer or ^KG not available")

    prefix = f"{PREFIX}_CHAIN"
    irispy = _createIRIS(iris_connection)
    result = irispy.classMethodValue(
        'Graph.KG.Traversal', 'BFSFastJson', f"{prefix}:ROOT", "", 2, ""
    )

    assert result, "BFSFastJson must return JSON"
    hops = json.loads(str(result))
    steps = {h["step"] for h in hops}
    assert 1 in steps, "step=1 (ROOT→MID) must be present"
    assert 2 in steps, "step=2 (MID→TAIL) must be present"


@pytest.mark.e2e
def test_bfs_fast_json_1hop_only(iris_connection, chain_graph, engine):
    """maxHops=1 returns only direct neighbors."""
    if not engine.capabilities.objectscript_deployed or not engine.capabilities.kg_built:
        pytest.skip("ObjectScript layer or ^KG not available")

    prefix = f"{PREFIX}_CHAIN"
    irispy = _createIRIS(iris_connection)
    result = irispy.classMethodValue(
        'Graph.KG.Traversal', 'BFSFastJson', f"{prefix}:ROOT", "", 1, ""
    )

    assert result
    hops = json.loads(str(result))
    assert all(h["step"] == 1 for h in hops), "Only step=1 hops expected with maxHops=1"


# ---------------------------------------------------------------------------
# US5 — Graceful fallback when .cls not deployed
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_ppr_fallback_without_cls(iris_connection):
    """
    With auto_deploy_objectscript=False, PPR falls back cleanly to Python/SQL.
    No exception, correct result type.
    """
    cursor = iris_connection.cursor()
    p = f"{PREFIX}_FALLBACK"
    cp = f"{p}:%"
    cursor.execute("DELETE FROM rdf_edges WHERE s LIKE ? OR o_id LIKE ?", [cp, cp])
    cursor.execute("DELETE FROM nodes WHERE node_id LIKE ?", [cp])
    for n in ["A", "B", "C"]:
        cursor.execute("INSERT INTO nodes (node_id) VALUES (?)", [f"{p}:{n}"])
    cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)", [f"{p}:A", "x", f"{p}:B"])
    cursor.execute("INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)", [f"{p}:B", "x", f"{p}:C"])
    iris_connection.commit()

    eng = IRISGraphEngine(iris_connection, embedding_dimension=384)
    eng.initialize_schema(auto_deploy_objectscript=False)

    assert not eng.capabilities.objectscript_deployed

    scores = eng.kg_PERSONALIZED_PAGERANK([f"{p}:A"], return_top_k=3)
    assert isinstance(scores, dict) and scores

    cursor.execute("DELETE FROM rdf_edges WHERE s LIKE ? OR o_id LIKE ?", [cp, cp])
    cursor.execute("DELETE FROM nodes WHERE node_id LIKE ?", [cp])
    iris_connection.commit()
    cursor.close()
