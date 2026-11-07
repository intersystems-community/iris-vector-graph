"""A large Arno reply comes back in chunks through process-private ^||LargeOut, over a tree this module loads itself.

The cases ran over whatever the session namespace held and skipped below 1,000 edges ("insufficient graph data"),
so on a fresh container (the gate's) they never ran. They now load their own graph into `IVG_CHUNKED_NAMESPACE`, a
scratch namespace with IVG installed that the E2E gate makes per run (tests/e2e/gate.py GROUPS), and assert the
answer, known by hand: a 10-ary tree under node_0 three levels deep (own_data_ns.tree, 1,110 edges), so a BFS from
node_0 of depth d reaches the nodes of levels 1..d (10, 110, 1,110), each at its level.

Which replies are chunked: `RandomWalkJson` (and `KHopJson`) store a reply over 9,000 characters in ^||LargeOut and
answer `CHUNKED:<tag>:<n>` (NKGAccelAdjacency.StoreLargeOut); 400 walks of four nodes is about 14,000 characters, 3
walks is inline JSON. `BFSJson` always answers `SORTED:<tag>` now (its hits staged in ^ArnoKG("bfs_r"), read by
`_call_classmethod_large`), so these cases no longer look for `CHUNKED:` on it: they did, and always took the inline
branch.
"""

import json
import os

import pytest

from tests.e2e import own_data_ns

NS_VAR = "IVG_CHUNKED_NAMESPACE"
SKIP_LARGE_OUT = os.environ.get("SKIP_ARNO_TESTS", "false").lower() == "true"
ARNO_LIB = os.environ.get("ARNO_LIB", "/usr/irissys/mgr/libarno_callout.so")
skip_reason = "SKIP_ARNO_TESTS=true"
K, DEPTH = 10, 3
TREE = own_data_ns.tree(K, DEPTH)
SEED = "node_0"


@pytest.fixture(scope="module")
def iris_conn():
    import iris as iris_mod

    from iris_vector_graph import IRISGraphEngine

    c, _ns = own_data_ns.connect(NS_VAR)
    engine = IRISGraphEngine(c, embedding_dimension=768)
    engine.erase_all()
    own_data_ns.load_tree(engine, TREE, "CHUNK")
    o = iris_mod.createIRIS(c)
    yield c, o
    engine.erase_all()
    c.close()


@pytest.fixture(scope="module")
def graph_seed(iris_conn):
    _c, o = iris_conn
    kill_arno_cache(o)
    assert int(o.classMethodValue("Graph.KG.NKGAccel", "IsLoaded")) == 1, f"Arno did not load from {ARNO_LIB}"
    return SEED


def kill_arno_cache(o):
    o.classMethodVoid("Graph.KG.NKGAccel", "ResetCache")
    o.classMethodValue("Graph.KG.NKGAccel", "Load", ARNO_LIB)


def call_large(iris_obj, cls, method, *args):
    from iris_vector_graph.schema import _call_classmethod_large
    return _call_classmethod_large(iris_obj, cls, method, *args)


def _hits(rows) -> set:
    return {(r["s"], r["o"], int(r["step"])) for r in rows}


PARENT = {o_: s_ for s_, o_, _ in TREE}


def _walks_down_the_tree(walks, seed) -> bool:
    """Each walk: the seed, then each node a child of the one before, to a leaf (level 3, no out-edge)."""
    return all(w[0] == seed and len(w) == DEPTH + 1 and all(PARENT[b] == a for a, b in zip(w, w[1:])) for w in walks)


@pytest.mark.skipif(SKIP_LARGE_OUT, reason=skip_reason)
class TestLargeOutputChunked:

    def test_sc001_bfs_json_returns_full_count_not_capped(self, iris_conn, graph_seed):
        _c, o = iris_conn
        kill_arno_cache(o)
        results = json.loads(call_large(o, "Graph.KG.NKGAccel", "BFSJson", graph_seed, "[]", 2, 0))
        # all 110 nodes within two hops, not the 50 the old cap allowed
        assert _hits(results) == own_data_ns.bfs_by_hand(TREE, 2)
        assert len({r["o"] for r in results}) == 110

    def test_sc003_random_walk_returns_all_entries(self, iris_conn, graph_seed):
        _c, o = iris_conn
        result = json.loads(call_large(o, "Graph.KG.NKGAccel", "RandomWalkJson", graph_seed, 20, 10))
        assert isinstance(result, list), "SC-003: RandomWalkJson must return a list"
        assert len(result) == 10, f"SC-003 fail: expected 10 walks, got {len(result)}"
        assert _walks_down_the_tree(result, graph_seed), result

    def test_sc004_inline_path_no_second_call_needed(self, iris_conn, graph_seed):
        _c, o = iris_conn
        raw = str(o.classMethodValue("Graph.KG.NKGAccel", "RandomWalkJson", graph_seed, 20, 3))
        assert raw.startswith("["), f"SC-004: three walks come back inline, in one call: {raw[:60]}"
        walks = json.loads(raw)
        assert len(walks) == 3 and _walks_down_the_tree(walks, graph_seed), walks
        small = json.loads(call_large(o, "Graph.KG.NKGAccel", "BFSJson", graph_seed, "[]", 1, 5))
        assert len(small) == 5 and _hits(small) <= own_data_ns.bfs_by_hand(TREE, 1), small

    def test_sc005_call_large_handles_both_inline_and_chunked(self, iris_conn, graph_seed):
        _c, o = iris_conn
        inline = json.loads(call_large(o, "Graph.KG.NKGAccel", "RandomWalkJson", graph_seed, 20, 3))
        assert len(inline) == 3 and _walks_down_the_tree(inline, graph_seed), inline
        raw = str(o.classMethodValue("Graph.KG.NKGAccel", "RandomWalkJson", graph_seed, 20, 400))
        assert raw.startswith("CHUNKED:RW:") and int(raw.split(":")[2]) > 1, (
            f"SC-005: 400 walks must come back chunked, or this case tests the inline path twice: {raw[:60]}"
        )
        chunked = json.loads(call_large(o, "Graph.KG.NKGAccel", "RandomWalkJson", graph_seed, 20, 400))
        assert len(chunked) == 400 and _walks_down_the_tree(chunked, graph_seed)
        large = json.loads(call_large(o, "Graph.KG.NKGAccel", "BFSJson", graph_seed, "[]", 3, 0))
        assert _hits(large) == own_data_ns.bfs_by_hand(TREE, 3)

    def test_sc006_process_private_isolation(self, iris_conn, graph_seed):
        import iris as iris_mod
        _c1, o1 = iris_conn
        c2, _ns = own_data_ns.connect(NS_VAR)
        try:
            o2 = iris_mod.createIRIS(c2)
            raw1 = str(o1.classMethodValue("Graph.KG.NKGAccel", "RandomWalkJson", graph_seed, 20, 400))
            assert raw1.startswith("CHUNKED:RW:"), f"SC-006: 400 walks must come back chunked: {raw1[:60]}"
            tag = raw1.split(":")[1]
            assert str(o1.classMethodValue("Graph.KG.NKGAccel", "ReadLargeOutChunk", tag, 1)).startswith('[["'), (
                "SC-006: the process that made the reply reads its first chunk"
            )
            # the driver reads IRIS's "" as None
            chunk_from_c2 = o2.classMethodValue("Graph.KG.NKGAccel", "ReadLargeOutChunk", tag, 1)
            assert chunk_from_c2 in ("", None), (
                f"SC-006 fail: ^||LargeOut visible across connections — not process-private! Got: {str(chunk_from_c2)[:50]}"
            )
        finally:
            c2.close()

    def test_sc007_removing_cap_does_not_break_arno_bfs_tests(self, iris_conn, graph_seed):
        _c, o = iris_conn
        kill_arno_cache(o)
        results = json.loads(call_large(o, "Graph.KG.NKGAccel", "BFSJson", graph_seed, "[]", 2, 0))
        assert all({"s", "p", "o", "w", "step"} <= set(r) for r in results), (
            "SC-007: result schema must match {s, p, o, w, step}"
        )
        assert _hits(results) == own_data_ns.bfs_by_hand(TREE, 2)

    def test_each_bfs_hit_names_its_relationship_type(self, iris_conn, graph_seed):
        """Arno 1.2.1's kg_bfs_global answers p 'R' for every hit; BFSJson re-reads each hit's type from ^KG("out")
        as it stages them (03476aed). Every hit of the tree is a CHUNK edge, on the library's route, as on the
        ObjectScript one."""
        _c, o = iris_conn
        kill_arno_cache(o)
        native = json.loads(call_large(o, "Graph.KG.NKGAccel", "BFSJson", graph_seed, "[]", 3, 0))
        assert str(o.classMethodValue("Graph.KG.NKGAccelTraversal", "LastBfsPath")) == "native"
        assert len(native) == len(TREE) and {r["p"] for r in native} == {"CHUNK"}
        objectscript = json.loads(call_large(o, "Graph.KG.Traversal", "BFSFastJsonSorted", graph_seed, "[]", 3, "",
                                             "out", 0))
        assert {(r["s"], r["p"], r["o"]) for r in native} == {(r["s"], r["p"], r["o"]) for r in objectscript}

    def test_store_large_out_method_exists(self, iris_conn):
        _c, o = iris_conn
        exists = str(o.classMethodValue(
            "%Dictionary.CompiledMethod",
            "%ExistsId",
            "Graph.KG.NKGAccel||StoreLargeOut"
        ))
        assert exists == "1", "StoreLargeOut must be compiled into NKGAccel"

    def test_read_large_out_chunk_method_exists(self, iris_conn):
        _c, o = iris_conn
        exists = str(o.classMethodValue(
            "%Dictionary.CompiledMethod",
            "%ExistsId",
            "Graph.KG.NKGAccel||ReadLargeOutChunk"
        ))
        assert exists == "1", "ReadLargeOutChunk must be compiled into NKGAccel"
