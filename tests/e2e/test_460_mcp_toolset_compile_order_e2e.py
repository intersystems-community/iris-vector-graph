"""4.6.0, live: the packaged classes compile into a fresh namespace with Graph.KG.MCPToolSet first, five times, 0 errors.

`%AI.ToolSet`'s `%Discover`/`%Invoke` generators read Graph.KG.MCPTools, the class MCPToolSet's XData includes. With
no `DependsOn` the compiler could run them first: on a fresh dpgenai1 gate container (2026-11-07) the first compile of
the package failed with ERROR #5289 ("MCPTools ... has not had inheritance resolved") and the rerun passed. Each
round here makes the namespace anew (IVG_MCP_ORDER_NAMESPACE, IVG460FX* only), creates the tables without classes,
loads every packaged class without compiling it, and then:

* compiles Graph.KG.MCPToolSet alone, MCPTools loaded and not compiled: the order that raced, made certain. Before
  `DependsOn` this failed every time; now it compiles MCPTools first;
* compiles the whole package as initialize_schema's deploy batches it (functional-index types, the rest, SqlProcs),
  one pass with no retry, MCPToolSet first in its batch.

Both must answer an OK status with no error text, and every class must then be up to date.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = os.environ.get("IVG_MCP_ORDER_NAMESPACE", "")
if not NS:  # the E2E gate names one per run (tests/e2e/gate.py GROUPS); never a fixed default
    pytest.skip("set IVG_MCP_ORDER_NAMESPACE to a scratch namespace of this run", allow_module_level=True)
ROUNDS = 5
FIRST = "Graph.KG.MCPToolSet"


def _status(nat, st) -> str:
    """'' for an OK %Status, else its error text."""
    if nat.classMethodValue("%SYSTEM.Status", "IsOK", st):
        return ""
    return str(nat.classMethodValue("%SYSTEM.Status", "GetErrorText", st))


def _load_without_compiling(nat, sources: dict) -> None:
    from iris_vector_graph._engine import class_deploy as cd

    for name, text in sources.items():
        stream = nat.classMethodObject("%Stream.GlobalCharacter", "%New")
        for i in range(0, len(text), cd.STREAM_CHUNK):
            stream.invoke("Write", text[i : i + cd.STREAM_CHUNK])
        stream.invoke("Rewind")
        err = _status(nat, nat.classMethodValue("%SYSTEM.OBJ", "LoadStream", stream, "-d"))
        assert err == "", f"{name} did not load: {err}"


def _batches(names: list) -> list:
    """initialize_schema's batches (class_deploy.upload_and_compile), with MCPToolSet first in its own."""
    from iris_vector_graph._engine import class_deploy as cd

    first = sorted(n for n in names if cd._FUNCTIONAL_INDEX.search(SOURCES[n]))
    last = sorted(n for n in names if n.rsplit(".", 1)[-1] == cd.FORWARDER_CLASS and n not in first)
    rest = sorted((n for n in names if n not in first and n not in last), key=lambda n: (n != FIRST, n))
    return [b for b in (first, rest, last) if b]


def _sources() -> dict:
    from iris_vector_graph._engine import class_deploy as cd

    return cd.rewrite_sources(cd.read_class_sources(cd.packaged_class_dir()), "Graph_KG")


SOURCES = _sources()


@pytest.fixture(scope="module")
def ns_guard():
    if not NS.startswith("IVG460FX"):
        pytest.fail(f"refusing {NS}: this file recreates its namespace, an IVG460FX* scratch namespace only")
    yield NS


@pytest.mark.parametrize("round_", range(1, ROUNDS + 1))
def test_the_package_compiles_with_the_toolset_first(ns_guard, round_):
    import iris

    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph._engine import class_deploy as cd

    ns262.recreate(NS)
    conn = ns262.connect(NS)
    try:
        nat = iris.createIRIS(conn)
        IRISGraphEngine(conn, embedding_dimension=8, namespace=NS).initialize_schema(auto_deploy_objectscript=False)
        names, skipped = cd.select_classes(SOURCES, embedded_python=cd.embedded_python_available(nat),
                                           class_exists=lambda n: cd.class_exists(nat, n))
        assert FIRST in names and "Graph.KG.MCPTools" in names, skipped  # an enterprise server compiles both
        _load_without_compiling(nat, {n: SOURCES[n] for n in names})
        assert not nat.classMethodValue("%SYSTEM.OBJ", "IsUpToDate", "Graph.KG.MCPTools")

        alone = _status(nat, nat.classMethodValue("%SYSTEM.OBJ", "CompileList", f"{FIRST}.cls", "ck-d"))
        assert alone == "", f"round {round_}: {FIRST} compiled alone: {alone}"
        whole = [_status(nat, nat.classMethodValue("%SYSTEM.OBJ", "CompileList",
                                                    ",".join(f"{n}.cls" for n in batch), "ck-d"))
                 for batch in _batches(names)]
        assert [e for e in whole if e] == [], f"round {round_}: the package, one pass: {whole}"
        stale = [n for n in names if not nat.classMethodValue("%SYSTEM.OBJ", "IsUpToDate", n)]
        assert stale == [], f"round {round_}: not up to date after the compile: {stale}"
    finally:
        conn.close()
