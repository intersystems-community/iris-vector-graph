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
