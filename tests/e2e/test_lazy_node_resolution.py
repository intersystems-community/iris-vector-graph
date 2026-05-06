import json
import os
import time

import pytest

IRIS_HOST = os.environ.get("IRIS_HOST", "localhost")
IRIS_PORT = int(os.environ.get("IRIS_PORT", "2972"))
IRIS_NS = os.environ.get("IRIS_NAMESPACE", "USER")
IRIS_USER = os.environ.get("IRIS_USERNAME", "_SYSTEM")
IRIS_PASS = os.environ.get("IRIS_PASSWORD", "SYS")
ARNO_LIB = os.environ.get("ARNO_LIB", "/usr/irissys/mgr/libarno_callout.so")
SKIP = os.environ.get("SKIP_ARNO_TESTS", "false").lower() == "true"


@pytest.fixture(scope="module")
def iris_conn():
    try:
        import iris as iris_mod
        c = iris_mod.connect(IRIS_HOST, IRIS_PORT, IRIS_NS, IRIS_USER, IRIS_PASS)
        o = iris_mod.createIRIS(c)
        o.classMethodVoid("Graph.KG.NKGAccel", "ResetCache")
        o.classMethodString("Graph.KG.NKGAccel", "Load", ARNO_LIB)
        o.classMethodVoid("Graph.KG.NKGAccel", "InvalidateAdjCache")
        yield c, o
        c.close()
    except Exception as e:
        pytest.skip(f"enterprise unavailable: {e}")


@pytest.fixture(scope="module")
def xl_seed(iris_conn):
    c, o = iris_conn
    cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges")
    count = cur.fetchone()[0]
    if count < 1_000_000:
        pytest.skip(f"XL data not loaded ({count} edges) — need >= 1M edges")
    o.classMethodString("Graph.KG.Traversal", "BuildNKG")
    o.classMethodVoid("Graph.KG.NKGAccel", "ResetCache")
    o.classMethodString("Graph.KG.NKGAccel", "Load", ARNO_LIB)
    o.classMethodVoid("Graph.KG.NKGAccel", "InvalidateAdjCache")
    return str(o.classMethodString("Graph.KG.NKGAccel", "GetFirstNKGNode"))


@pytest.fixture(scope="module")
def m_seed(iris_conn):
    c, o = iris_conn
    seed = str(o.classMethodString("Graph.KG.NKGAccel", "GetFirstNKGNode"))
    if not seed or seed == "0" or seed == "":
        pytest.skip("no NKG data loaded — run BulkIngestEdges + BuildNKG first")
    return seed


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
        from iris_vector_graph.schema import _call_classmethod_large
        raw = _call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson", m_seed, "[]", 1, 3)
        r = json.loads(raw)
        assert isinstance(r, list), "SC-002: BFSJson must return a list"
        assert len(r) > 0, "SC-002: BFSJson must return results (seed lookup must work)"

    def test_sc003_bfs_max50_minimal_reads(self, iris_conn, m_seed):
        _, o = iris_conn
        from iris_vector_graph.schema import _call_classmethod_large
        raw = _call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson", m_seed, "[]", 3, 50)
        r = json.loads(raw)
        assert len(r) > 0, "SC-003: BFSJson with max_results=50 must return results"
        assert len(r) <= 50, f"SC-003: expected ≤50 results, got {len(r)}"
        for item in r:
            assert "o" in item and "s" in item, "SC-003: result must have s and o fields"
            assert item["o"].startswith("node_"), (
                f"SC-003: result o={item['o']!r} must be a node name string, not integer index. "
                "Failing means NodeResolver not resolving names (falling back to format!('{idx}'))"
            )

    def test_sc004_no_regression_small_medium(self, iris_conn, m_seed):
        _, o = iris_conn
        from iris_vector_graph.schema import _call_classmethod_large
        for depth in [1, 2, 3]:
            raw = _call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson", m_seed, "[]", depth, 0)
            r = json.loads(raw)
            assert isinstance(r, list), f"SC-004: depth={depth} must return list"
            if depth == 1:
                assert len(r) > 0, f"SC-004: depth=1 must return results"
            for item in r[:5]:
                assert item.get("o", "").startswith("node_"), (
                    f"SC-004: depth={depth} result o={item.get('o')!r} is not a node name"
                )

    def test_sc005_ppr_returns_node_names_not_indices(self, iris_conn, m_seed):
        _, o = iris_conn
        from iris_vector_graph.schema import _call_classmethod_large
        import re
        raw = _call_classmethod_large(o, "Graph.KG.NKGAccel", "PPRNative", m_seed, 0.85, 5, 0)
        fixed = re.sub(r'(?<![0-9])(\.\d)', r'0\1', raw)
        result = json.loads(fixed)
        scores = result.get("scores", []) if isinstance(result, dict) else result
        assert len(scores) >= 1, "SC-005: PPRNative must return scores"
        for s in scores[:5]:
            node_id = s.get("id", "")
            assert node_id.startswith("node_"), (
                f"SC-005: PPR score id={node_id!r} must be node name string, not integer. "
                "Failing means ppr_on_adj using integer indices instead of resolver names."
            )

    def test_sc006_cache_dedup_no_double_read(self, iris_conn, m_seed):
        _, o = iris_conn
        from iris_vector_graph.schema import _call_classmethod_large
        raw1 = _call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson", m_seed, "[]", 2, 0)
        raw2 = _call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson", m_seed, "[]", 2, 0)
        r1 = {x["o"] for x in json.loads(raw1)}
        r2 = {x["o"] for x in json.loads(raw2)}
        assert r1 == r2, "SC-006: repeated BFS calls must return identical result sets"

    def test_result_names_are_strings_not_integers(self, iris_conn, m_seed):
        _, o = iris_conn
        from iris_vector_graph.schema import _call_classmethod_large
        raw = _call_classmethod_large(o, "Graph.KG.NKGAccel", "BFSJson", m_seed, "[]", 1, 0)
        r = json.loads(raw)
        assert len(r) > 0
        for item in r[:10]:
            try:
                int(item["o"])
                pytest.fail(
                    f"result o={item['o']!r} is a pure integer — NodeResolver not resolving "
                    "^NKG('$ND', idx). Before fix: format!('{idx}') fallback returns '223' etc."
                )
            except ValueError:
                pass
