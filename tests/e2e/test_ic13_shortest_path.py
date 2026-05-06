import json
import os
import time

import pytest

IRIS_HOST = os.environ.get("IRIS_HOST", "localhost")
IRIS_PORT = int(os.environ.get("IRIS_PORT", "4972"))
IRIS_NS = os.environ.get("IRIS_NAMESPACE", "USER")
IRIS_USER = os.environ.get("IRIS_USERNAME", "_SYSTEM")
IRIS_PASS = os.environ.get("IRIS_PASSWORD", "SYS")
SKIP = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


@pytest.fixture(scope="module")
def iris_conn():
    try:
        import iris as iris_mod
        c = iris_mod.connect(IRIS_HOST, IRIS_PORT, IRIS_NS, IRIS_USER, IRIS_PASS)
        o = iris_mod.createIRIS(c)
        yield c, o
        c.close()
    except Exception as e:
        pytest.skip(f"IRIS unavailable: {e}")


@pytest.fixture(scope="module")
def ldbc_data(iris_conn):
    c, o = iris_conn
    ni = str(o.classMethodString("Graph.KG.NKGAccel", "GetFirstNKGNode"))
    if not ni or ni == "None":
        pytest.skip("No NKG data — load LDBC SF1 knows graph first")
    if not ni.startswith("p_"):
        pytest.skip(f"NKG seed {ni!r} is not LDBC knows data (expected p_N)")
    return ni


@pytest.mark.skipif(SKIP, reason="SKIP_IRIS_TESTS=true")
class TestIC13ShortestPath:

    def test_sc001_method_exists(self, iris_conn):
        c, o = iris_conn
        exists = str(o.classMethodString(
            "%Dictionary.CompiledMethod", "%ExistsId",
            "Graph.KG.NKGAccel||ShortestPathNKG"
        ))
        assert exists == "1", "ShortestPathNKG must be compiled into NKGAccel"

    def test_sc002_correctness_matches_existing(self, iris_conn, ldbc_data):
        c, o = iris_conn
        pairs = [("p_933", "p_4139"), ("p_933", "p_10995116284808"),
                 ("p_10008", "p_6597069777240")]
        for src, dst in pairs:
            old = json.loads(str(o.classMethodString(
                "Graph.KG.Traversal", "ShortestPathJson", src, dst, 10, "[]", "both", 0)))
            new_raw = str(o.classMethodString(
                "Graph.KG.NKGAccel", "ShortestPathNKG", src, dst, 10))
            new = json.loads(new_raw)
            old_hops = old[0]["length"] if old else -1
            new_hops = new.get("hops", -2)
            assert old_hops == new_hops, (
                f"SC-002: {src}->{dst} old={old_hops} new={new_hops} mismatch"
            )

    def test_sc003_3hop_under_20ms(self, iris_conn, ldbc_data):
        c, o = iris_conn
        src, dst = "p_10008", "p_6597069777240"
        times = []
        for _ in range(10):
            t0 = time.perf_counter()
            o.classMethodString("Graph.KG.NKGAccel", "ShortestPathNKG", src, dst, 10)
            times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        p50 = times[len(times) // 2]
        assert p50 <= 20, (
            f"SC-003: 3-hop ShortestPathNKG p50={p50:.1f}ms > 20ms target. "
            f"Old ShortestPathJson was 175ms. Layer 2 bidir BFS must fix this."
        )

    def test_sc004_no_path_returns_minus1(self, iris_conn):
        c, o = iris_conn
        result = json.loads(str(o.classMethodString(
            "Graph.KG.NKGAccel", "ShortestPathNKG", "p_nonexistent", "p_also_fake", 10)))
        assert result.get("hops") == -1, "SC-004: nonexistent nodes must return hops=-1"

    def test_sc005_same_node_returns_0(self, iris_conn, ldbc_data):
        c, o = iris_conn
        result = json.loads(str(o.classMethodString(
            "Graph.KG.NKGAccel", "ShortestPathNKG", "p_933", "p_933", 10)))
        assert result.get("hops") == 0, "SC-005: same src and dst must return hops=0"

    def test_sc006_far_pair_under_200ms(self, iris_conn, ldbc_data):
        c, o = iris_conn
        src, dst = "p_10008", "p_6194"
        times = []
        for _ in range(5):
            t0 = time.perf_counter()
            o.classMethodString("Graph.KG.NKGAccel", "ShortestPathNKG", src, dst, 10)
            times.append((time.perf_counter() - t0) * 1000)
        times.sort()
        p50 = times[len(times) // 2]
        assert p50 <= 200, (
            f"SC-006: far-pair ShortestPathNKG p50={p50:.1f}ms > 200ms target"
        )
