"""A call into the Arno library leaves the process in the caller's namespace (found 2026-10-25, 4.4.0 E2E gate).

The library's readers read USER's globals and leave the process in USER. `ArnoAccel.Load`, `NativeResult` and
`TraversalBuild.Build2HopExactStats` already put the namespace back or refuse, but `ArnoAccel.CallFn` (behind
`KhopSampleJson`, `RandomWalkJson`, `NeighborAggJson`) and a direct `NKGAccelTraversal.Build2HopExact` did not. One
such call in a non-USER namespace moved the shared Native API process to USER, and everything after it on that
connection read and wrote USER: 135 E2E failures in a run with `IVG_NAMESPACE=IVGFIX`, all passing in USER. Outside
USER these entry points now refuse (they would answer from, or write into, USER's graph) and the namespace stays.

Needs a non-USER namespace with the classes deployed (IVG_NAMESPACE, IVG_SECONDARY_NAMESPACE, or the IVGPAROS
scratch namespace, the first that is not USER and exists) and the Arno library on the container.
"""

from __future__ import annotations

import json
import os

import pytest

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]


@pytest.fixture(scope="module")
def other_ns():
    import iris

    port = int(os.environ.get("IVG_PORT", "31972"))
    names = [os.environ.get(v, "") for v in ("IVG_NAMESPACE", "IVG_SECONDARY_NAMESPACE")]
    names.append(os.environ.get("IVG_PAROS_NAMESPACE", "IVGPAROS"))  # the scratch namespace BuildKG may rebuild
    conn = None
    for ns in (n for n in names if n and n.upper() != "USER"):
        try:
            conn = iris.connect("localhost", port, ns, "_SYSTEM", "SYS")
            break
        except Exception:
            continue
    if conn is None:
        pytest.skip("needs a non-USER namespace: IVG_NAMESPACE, IVG_SECONDARY_NAMESPACE or IVGPAROS")
    native = iris.createIRIS(conn)
    user = iris.createIRIS(iris.connect("localhost", port, "USER", "_SYSTEM", "SYS"))
    lib = user.get("ArnoAccel", "lib_path")
    if not lib or not native.classMethodValue("%File", "Exists", lib):
        pytest.skip("the Arno library is not installed on this container")
    assert native.classMethodValue("Graph.KG.ArnoAccel", "Load", lib) == 1
    native.classMethodValue("Graph.KG.NKGAccelLoader", "Load", lib)
    yield ns, native
    conn.close()


CALLS = [
    ("ArnoAccel", "KhopSampleJson", '["a"]', 2, 5),
    ("ArnoAccel", "RandomWalkJson", '["a"]', 5, 4),
    ("ArnoAccel", "NeighborAggJson", '["a"]', 2),
    ("NKGAccelTraversal", "Build2HopExact"),
    ("NKGAccel", "Build2HopExact"),
    ("Traversal", "BuildKG"),
    ("NKGAccelTraversal", "BFSJson", "a", "[]", 3, 0),
    ("NKGAccelTraversal", "CountDistinctKHop", "a", "", 2, "both"),
]


@pytest.mark.parametrize("call", CALLS, ids=[f"{c[0]}.{c[1]}" for c in CALLS])
def test_the_process_stays_in_the_callers_namespace(other_ns, call):
    ns, native = other_ns
    native.classMethodValue(f"Graph.KG.{call[0]}", call[1], *call[2:])
    assert native.classMethodValue("%SYSTEM.SYS", "NameSpace") == ns


def test_the_user_only_readers_refuse_outside_user(other_ns):
    """They read USER's graph: elsewhere they answer as unavailable, never with USER's nodes."""
    ns, native = other_ns
    khop = json.loads(str(native.classMethodValue("Graph.KG.ArnoAccel", "KhopSampleJson", '["a"]', 2, 5)))
    assert khop["layers"] == [] and "USER" in khop.get("error", ""), khop
    assert json.loads(str(native.classMethodValue("Graph.KG.ArnoAccel", "RandomWalkJson", '["a"]', 5, 4))) == []
    assert json.loads(str(native.classMethodValue("Graph.KG.ArnoAccel", "NeighborAggJson", '["a"]', 2))) == {}
    assert int(native.classMethodValue("Graph.KG.NKGAccelTraversal", "Build2HopExact")) == 0


def test_betweenness_neighborhood_answers_the_callers_graph_and_stays(other_ns):
    """R234: `BetweennessNeighborhood`'s Arno fast path hands the library "^NKG", which it reads in USER, and the call
    leaves the process there. On a fresh container (USER's ^NKG empty) a non-USER namespace got `OK:[]`, and every later
    call on the connection ran in USER: all of test_betweenness_neighborhood_e2e and, in a full run, much of what came
    after it on the session's Native API connection. Where USER held the same ids it answered from USER's graph.

    The path a - b - c, both directions: within 2 hops of b, b is the only node between two others."""
    import uuid

    import iris

    from iris_vector_graph.engine import IRISGraphEngine

    ns, native = other_ns
    conn = iris.connect("localhost", int(os.environ.get("IVG_PORT", "31972")), ns, "_SYSTEM", "SYS")
    engine = IRISGraphEngine(conn)
    p = f"bnh{uuid.uuid4().hex[:6]}"
    a, b, c = f"{p}:a", f"{p}:b", f"{p}:c"
    try:
        for n in (a, b, c):
            engine.create_node(n)
        for s, o in ((a, b), (b, a), (b, c), (c, b)):
            engine.create_edge(s, "R234_LINK", o)
        native.classMethodValue("Graph.KG.Traversal", "BuildNKG")
        assert native.classMethodValue("Graph.KG.NKGAccelLoader", "IsLoaded") == 1  # the fast path is offered
        raw = str(native.classMethodValue("Graph.KG.NKGAccel", "BetweennessNeighborhood", b, 2, 0, 10))
        assert native.classMethodValue("%SYSTEM.SYS", "NameSpace") == ns
        assert raw.startswith("OK:"), raw
        scores = {r["id"]: r["score"] for r in json.loads(raw[3:]) if r["score"]}
        assert scores == {b: 2}, scores  # (a, c) and (c, a), each through b
    finally:
        for n in (a, b, c):
            engine.delete_node(n)
        conn.close()
