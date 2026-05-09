import json
import os
import time

import pytest

IRIS_HOST = os.environ.get("IRIS_HOST", "localhost")
IRIS_PORT = int(os.environ.get("IVG_TEST_PORT", "2972"))
IRIS_NS = os.environ.get("IRIS_NAMESPACE", "USER")
IRIS_USER = os.environ.get("IRIS_USERNAME", "_SYSTEM")
IRIS_PASS = os.environ.get("IRIS_PASSWORD", "SYS")

SKIP_LARGE_OUT = os.environ.get("SKIP_ARNO_TESTS", "false").lower() == "true"
skip_reason = "SKIP_ARNO_TESTS=true or enterprise container unavailable"


@pytest.fixture(scope="module")
def iris_conn():
    try:
        import iris as iris_mod
        c = iris_mod.connect(IRIS_HOST, IRIS_PORT, IRIS_NS, IRIS_USER, IRIS_PASS)
        o = iris_mod.createIRIS(c)
        yield c, o
        c.close()
    except Exception as e:
        pytest.skip(f"{skip_reason}: {e}")


@pytest.fixture(scope="module")
def graph_seed(iris_conn):
    c, o = iris_conn
    cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM SQLUser.rdf_edges")
    count = cur.fetchone()[0]
    if count < 1000:
        pytest.skip("insufficient graph data — load at least 1K before running")
    o.classMethodValue("Graph.KG.Traversal", "BuildNKG")
    kill_arno_cache(o)
    seed = str(o.classMethodValue("Graph.KG.NKGAccel", "GetFirstNKGNode"))
    if not seed or seed == "0":
        seed = "node_0"
    return seed


def kill_arno_cache(o):
    try:
        o.classMethodVoid("Graph.KG.NKGAccel", "ResetCache")
    except Exception:
        pass
    try:
        o.classMethodValue("Graph.KG.NKGAccel", "Load", "/usr/irissys/mgr/libarno_callout.so")
    except Exception:
        pass


def call_large(iris_obj, cls, method, *args):
    from iris_vector_graph.schema import _call_classmethod_large
    return _call_classmethod_large(iris_obj, cls, method, *args)


@pytest.mark.skipif(SKIP_LARGE_OUT, reason=skip_reason)
class TestLargeOutputChunked:

    def test_sc001_bfs_json_returns_full_count_not_capped(self, iris_conn, graph_seed):
        c, o = iris_conn
        seed = graph_seed
        o.classMethodVoid("Graph.KG.NKGAccel", "ResetCache")
        try:
            o.classMethodValue("Graph.KG.NKGAccel", "Load", "/usr/irissys/mgr/libarno_callout.so")
        except Exception:
            pass
        try:
            raw = call_large(o, "Graph.KG.NKGAccel", "BFSJson", seed, "[]", 2, 0)
        except RuntimeError as e:
            if "DYNAMIC LIBRARY LOAD" in str(e) or "libarno_callout" in str(e):
                pytest.skip("arno .so not available on this container — SC-001 requires enterprise IRIS")
            raise
        results = json.loads(raw)
        count = len({r["o"] for r in results})
        assert count > 50, (
            f"SC-001 fail: BFSJson depth=2 returned {count} distinct nodes — "
            f"expected >50 (current cap=50 must be removed)"
        )

    def test_sc002_ppr_returns_full_node_scores(self, iris_conn, graph_seed):
        c, o = iris_conn
        seed = graph_seed
        raw = call_large(o, "Graph.KG.NKGAccel", "PPRNative", seed, 0.85, 5, 0)
        import re
        fixed = re.sub(r'(?<![0-9])(\.\d)', r'0\1', raw)
        result = json.loads(fixed)
        scores = result.get("scores", []) if isinstance(result, dict) else result
        assert isinstance(scores, list), "SC-002: PPRNative must return a list of scores"
        assert len(scores) >= 1, "SC-002: PPRNative must return at least 1 score"
        assert not raw.startswith("CHUNKED:"), (
            "SC-002: PPRNative result must not be a raw CHUNKED sentinel — "
            "StoreLargeOut must be called at the PPRJson boundary, not PPRNative"
        )

    def test_sc003_random_walk_returns_all_entries(self, iris_conn, graph_seed):
        c, o = iris_conn
        seed = graph_seed
        raw = call_large(o, "Graph.KG.NKGAccel", "RandomWalkJson", seed, 20, 10)
        result = json.loads(raw)
        assert isinstance(result, list), "SC-003: RandomWalkJson must return a list"
        assert len(result) == 10, (
            f"SC-003 fail: expected 10 walks, got {len(result)}"
        )
        for walk in result:
            assert len(walk) > 1, "each walk must have at least 2 entries (seed + steps)"

    def test_sc004_inline_path_no_second_call_needed(self, iris_conn, graph_seed):
        c, o = iris_conn
        seed = graph_seed
        try:
            raw = call_large(o, "Graph.KG.NKGAccel", "BFSJson", seed, "[]", 1, 5)
        except RuntimeError as e:
            if "DYNAMIC LIBRARY LOAD" in str(e) or "libarno_callout" in str(e):
                pytest.skip("arno .so not available — SC-004 requires enterprise IRIS")
            raise
        result = json.loads(raw)
        assert isinstance(result, list), "SC-004: small result must return inline JSON list"
        assert len(result) <= 5

    def test_sc005_call_large_handles_both_inline_and_chunked(self, iris_conn, graph_seed):
        c, o = iris_conn
        seed = graph_seed
        try:
            small = call_large(o, "Graph.KG.NKGAccel", "BFSJson", seed, "[]", 1, 3)
            assert json.loads(small) is not None, "SC-005: inline path must parse as JSON"
            large = call_large(o, "Graph.KG.NKGAccel", "BFSJson", seed, "[]", 3, 0)
            parsed = json.loads(large)
            assert isinstance(parsed, list), "SC-005: chunked path must reassemble as valid JSON list"
        except RuntimeError as e:
            if "DYNAMIC LIBRARY LOAD" in str(e) or "libarno_callout" in str(e):
                pytest.skip("arno .so not available — SC-005 requires enterprise IRIS")
            raise

    def test_sc006_process_private_isolation(self, iris_conn):
        import iris as iris_mod
        c1, o1 = iris_conn
        try:
            c2 = iris_mod.connect(IRIS_HOST, IRIS_PORT, IRIS_NS, IRIS_USER, IRIS_PASS)
            o2 = iris_mod.createIRIS(c2)

            cur = c1.cursor()
            cur.execute("SELECT TOP 1 s FROM SQLUser.rdf_edges GROUP BY s ORDER BY COUNT(*) DESC")
            seed = cur.fetchone()[0]

            o1.classMethodVoid("Graph.KG.NKGAccel", "ResetCache")
            try:
                o1.classMethodValue("Graph.KG.NKGAccel", "Load", "/usr/irissys/mgr/libarno_callout.so")
            except Exception:
                pass
            raw1 = str(o1.classMethodValue("Graph.KG.NKGAccel", "BFSJson", seed, "[]", 3, 0))

            if raw1.startswith("CHUNKED:"):
                _, tag, _ = raw1.split(":", 2)
                chunk_from_c2 = str(o2.classMethodValue("Graph.KG.NKGAccel", "ReadLargeOutChunk", tag, 1))
                assert chunk_from_c2 == "", (
                    f"SC-006 fail: ^||LargeOut visible across connections — "
                    f"not process-private! Got: {chunk_from_c2[:50]}"
                )
            c2.close()
        except Exception as e:
            if "SC-006 fail" in str(e):
                raise
            pytest.skip(f"SC-006 setup failed: {e}")

    def test_sc007_removing_cap_does_not_break_arno_bfs_tests(self, iris_conn, graph_seed):
        c, o = iris_conn
        seed = graph_seed
        o.classMethodVoid("Graph.KG.NKGAccel", "ResetCache")
        try:
            o.classMethodValue("Graph.KG.NKGAccel", "Load", "/usr/irissys/mgr/libarno_callout.so")
        except Exception:
            pass
        try:
            raw = call_large(o, "Graph.KG.NKGAccel", "BFSJson", seed, "[]", 2, 0)
        except RuntimeError as e:
            if "DYNAMIC LIBRARY LOAD" in str(e) or "libarno_callout" in str(e):
                pytest.skip("arno .so not available — SC-007 requires enterprise IRIS")
            raise
        results = json.loads(raw)
        assert isinstance(results, list), "SC-007: BFSJson must still return a list"
        assert all("o" in r and "s" in r and "step" in r for r in results[:5]), (
            "SC-007: result schema must match {s, p, o, w, step}"
        )

    def test_store_large_out_method_exists(self, iris_conn):
        c, o = iris_conn
        exists = str(o.classMethodValue(
            "%Dictionary.CompiledMethod",
            "%ExistsId",
            "Graph.KG.NKGAccel||StoreLargeOut"
        ))
        assert exists == "1", "StoreLargeOut must be compiled into NKGAccel"

    def test_read_large_out_chunk_method_exists(self, iris_conn):
        c, o = iris_conn
        exists = str(o.classMethodValue(
            "%Dictionary.CompiledMethod",
            "%ExistsId",
            "Graph.KG.NKGAccel||ReadLargeOutChunk"
        ))
        assert exists == "1", "ReadLargeOutChunk must be compiled into NKGAccel"
