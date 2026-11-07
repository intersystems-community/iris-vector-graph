"""Arno's BFS resolves ^NKG indices to node names lazily (spec 230), over a tree this module loads itself.

The medium cases ran over whatever the session namespace held and skipped when it was not a `node_<n>` load, so on
a fresh container (the gate's) they never ran. They now load their own graph into `IVG_LAZY_NODES_NAMESPACE`, a
scratch namespace with IVG installed that the E2E gate makes per run (tests/e2e/gate.py GROUPS), and assert the
answer, known by hand: a 10-ary tree under node_0 three levels deep (node i's children are node_10i+1 ..
node_10i+10; 1,110 edges, own_data_ns.tree), so a BFS from node_0 of depth d reaches exactly the nodes of levels
1..d, each at its level.

The XL case (a cold start under 500 ms on at least 1M edges) needs a load of that size; it skips without one, and
the gate allows that skip (gate_skips.txt): a timing bound measured under a full suite's load is no benchmark
(docs/dev/TESTING_POLICY.md, Environment).
"""

import json
import os
import time

import pytest

from tests.e2e import own_data_ns

NS_VAR = "IVG_LAZY_NODES_NAMESPACE"
ARNO_LIB = os.environ.get("ARNO_LIB", "/usr/irissys/mgr/libarno_callout.so")
SKIP = os.environ.get("SKIP_ARNO_TESTS", "false").lower() == "true"


#: The node-id prefix of the load these tests are written against. The assertions
#: read it directly (`o.startswith("node_")`) to tell a resolved name from the
#: `format!("{idx}")` fallback, so a namespace holding some other dataset cannot
#: satisfy them however healthy it is.
BENCHMARK_ID_PREFIX = "node_"


def why_the_benchmark_dataset_is_missing(seed: str, edge_count: int):
    """Why the lazy-resolution load is not in this namespace, or None if it is.

    `^NKG` outlives the rows it indexes, so a seed on its own proves nothing: the
    gate found `GetFirstNKGNode` answering `'SGS_eefd73_A'` in a namespace with
    zero `Graph_KG.rdf_edges`. Every assertion in this file then failed on data
    that was never loaded, and none of those failures was about lazy resolution.
    """
    if not seed or seed in ("0", "None"):
        # GetFirstNKGNode answers "0" or None (read as 'None') when ^NKG has no node: nothing was indexed, which is
        # not a stale index (the gate's message said "stale" for 'None', 2026-11-07)
        return f"^NKG has no node (GetFirstNKGNode answered {seed!r}): no data loaded, run BulkIngestEdges + BuildNKG"
    if not edge_count:
        return (
            f"^NKG still answers with the seed {seed!r} but Graph_KG.rdf_edges is "
            "empty: the index is stale, left behind by a load that has since been "
            "erased. Run BulkIngestEdges + BuildNKG to measure lazy resolution."
        )
    if not seed.startswith(BENCHMARK_ID_PREFIX):
        return (
            f"^NKG's first node is {seed!r}, not a {BENCHMARK_ID_PREFIX!r} id, so "
            f"this namespace holds another suite's {edge_count} edges. These "
            "assertions read node names from the benchmark load."
        )
    return None


K, DEPTH = 10, 3
TREE = own_data_ns.tree(K, DEPTH, prefix=BENCHMARK_ID_PREFIX)
SEED = f"{BENCHMARK_ID_PREFIX}0"


@pytest.fixture(scope="module")
def iris_conn():
    import iris as iris_mod

    from iris_vector_graph import IRISGraphEngine

    c, _ns = own_data_ns.connect(NS_VAR)
    engine = IRISGraphEngine(c, embedding_dimension=768)
    engine.erase_all()
    own_data_ns.load_tree(engine, TREE, "LAZY")
    o = iris_mod.createIRIS(c)
    o.classMethodVoid("Graph.KG.NKGAccel", "ResetCache")
    assert int(o.classMethodValue("Graph.KG.NKGAccel", "Load", ARNO_LIB)) == 1, f"Arno did not load from {ARNO_LIB}"
    o.classMethodVoid("Graph.KG.NKGAccel", "InvalidateAdjCache")
    yield c, o
    engine.erase_all()
    c.close()


@pytest.fixture(scope="module")
def xl_seed(iris_conn):
    c, o = iris_conn
    cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges")
    count = cur.fetchone()[0]
    if count < 1_000_000:
        pytest.skip(f"XL data not loaded ({count} edges) — need >= 1M edges")
    o.classMethodValue("Graph.KG.Traversal", "BuildNKG")
    o.classMethodVoid("Graph.KG.NKGAccel", "ResetCache")
    o.classMethodValue("Graph.KG.NKGAccel", "Load", ARNO_LIB)
    o.classMethodVoid("Graph.KG.NKGAccel", "InvalidateAdjCache")
    return str(o.classMethodValue("Graph.KG.NKGAccel", "GetFirstNKGNode"))


@pytest.fixture(scope="module")
def m_seed(iris_conn):
    c, o = iris_conn
    seed = str(o.classMethodValue("Graph.KG.NKGAccel", "GetFirstNKGNode"))
    cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges")
    edge_count = cur.fetchone()[0]
    cur.close()
    assert edge_count == len(TREE), edge_count
    # this module loaded the data: a missing dataset now is a failure, not a skip
    missing = why_the_benchmark_dataset_is_missing(seed, edge_count)
    assert missing is None, missing
    return SEED


def _bfs(o, depth: int, max_results: int) -> list:
    from iris_vector_graph.schema import _call_classmethod_large
    return json.loads(_call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson", SEED, "[]", depth, max_results))


def _hits(rows) -> set:
    return {(r["s"], r["o"], int(r["step"])) for r in rows}


@pytest.mark.skipif(SKIP, reason="SKIP_ARNO_TESTS=true")
class TestLazyNodeResolution:

    def test_sc001_xl_cold_start_under_500ms(self, iris_conn, xl_seed):
        _, o = iris_conn
        from iris_vector_graph.schema import _call_classmethod_large
        t0 = time.perf_counter()
        raw = _call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson", xl_seed, "[]", 1, 0)
        ms = (time.perf_counter() - t0) * 1000
        r = json.loads(raw)
        count = len({x["o"] for x in r})
        assert count > 0, f"SC-001: XL BFS returned 0 results — NodeResolver seed lookup failed"
        assert ms < 500, (
            f"SC-001: XL cold BFS took {ms:.0f}ms — expected < 500ms after lazy NodeResolver. "
            f"Before fix this was ~57ms pre-load + BFS. Failing means eager pre-load still active."
        )

    def test_sc002_seed_lookup_is_one_read(self, iris_conn, m_seed):
        _, o = iris_conn
        r = _bfs(o, 1, 3)
        assert isinstance(r, list), "SC-002: BFSJson must return a list"
        # three of node_0's ten children, at step 1 (the seed lookup worked and the names resolved)
        assert len(r) == 3 and _hits(r) <= own_data_ns.bfs_by_hand(TREE, 1), r

    def test_sc003_bfs_max50_minimal_reads(self, iris_conn, m_seed):
        _, o = iris_conn
        r = _bfs(o, 3, 50)
        assert len(r) == 50, f"SC-003: expected 50 of the 1,110 hits within 3 hops, got {len(r)}"
        for item in r:
            assert item["o"].startswith("node_"), (
                f"SC-003: result o={item['o']!r} must be a node name string, not integer index. "
                "Failing means NodeResolver not resolving names (falling back to format!('{idx}'))"
            )
        assert _hits(r) <= own_data_ns.bfs_by_hand(TREE, 3), r

    def test_sc004_no_regression_small_medium(self, iris_conn, m_seed):
        _, o = iris_conn
        for depth in [1, 2, 3]:
            r = _bfs(o, depth, 0)
            assert isinstance(r, list), f"SC-004: depth={depth} must return list"
            # every edge down to that level, each child at its level: 10, 110, 1,110 hits
            assert _hits(r) == own_data_ns.bfs_by_hand(TREE, depth), f"SC-004: depth={depth}"
            assert len(r) == sum(K ** d for d in range(1, depth + 1))

    def test_sc006_cache_dedup_no_double_read(self, iris_conn, m_seed):
        _, o = iris_conn
        r1, r2 = _hits(_bfs(o, 2, 0)), _hits(_bfs(o, 2, 0))
        assert r1 == r2 == own_data_ns.bfs_by_hand(TREE, 2), "SC-006: repeated BFS calls must return the same answer"

    def test_result_names_are_strings_not_integers(self, iris_conn, m_seed):
        _, o = iris_conn
        r = _bfs(o, 1, 0)
        assert {item["o"] for item in r} == {f"node_{i}" for i in range(1, K + 1)}, (
            "NodeResolver not resolving ^NKG('$ND', idx): before the fix format!('{idx}') answered '223' etc."
        )
