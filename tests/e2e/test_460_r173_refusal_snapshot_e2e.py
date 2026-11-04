"""4.6.0 R173: on a namespace recorded as `RAG`, every entry point that deploys IVG's classes or creates its SQL objects
for `Graph_KG` raises before it changes anything.

fixes17 saw IVG460FXD, recorded `RAG`, holding unrewritten `Graph_KG` schema classes (`Graph.KG.SqlProcs`,
`LedgerRevision`, `LedgerStats`) after a `test_460_pyops_search_e2e.py` session. The namespace here (IVG460FXE, or
IVG_R173_NAMESPACE, IVG460FX* only) is made anew with nothing in it, then set up by this release with
`schema_prefix="RAG"`, which records `RAG`. Each test takes a whole-namespace snapshot (the class list, `^oddDEF`'s
top keys, `INFORMATION_SCHEMA.TABLES` and `ROUTINES`, every base table's row count, and digests of `^IVG.Deploy` and
IVG's other globals), runs one entry point as a `Graph_KG` caller would, and compares a second snapshot with it.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.e2e import ns262

pytestmark = [pytest.mark.e2e, ns262.requires_container]

NS = os.environ.get("IVG_R173_NAMESPACE", "IVG460FXE")
REPO = Path(__file__).resolve().parents[2]


def _oddDEF_keys(nat) -> list:
    """`^oddDEF`'s top subscripts after the %-classes (which sort first and live in the system databases)."""
    keys, k = [], nat.nextSubscript(False, "^oddDEF", "&")
    while k:
        keys.append(k)
        k = nat.nextSubscript(False, "^oddDEF", k)
    return keys


def snapshot(conn) -> dict:
    snap = ns262.deep_snapshot(conn)
    nat = ns262.native(conn)
    snap["oddDEF"] = _oddDEF_keys(nat)
    snap["record"] = nat.get("^IVG.Deploy", "schema")
    return snap


def _diff(before: dict, after: dict) -> dict:
    out = {}
    for key in before.keys() | after.keys():
        b, a = before.get(key), after.get(key)
        if b == a:
            continue
        if isinstance(b, list) and isinstance(a, list):
            out[key] = {"added": sorted(set(map(str, a)) - set(map(str, b)))[:20],
                        "removed": sorted(set(map(str, b)) - set(map(str, a)))[:20]}
        else:
            out[key] = (b, a) if not isinstance(b, dict) else {k: (b.get(k), a.get(k)) for k in b.keys() | a.keys()
                                                               if b.get(k) != a.get(k)}
    return out


@pytest.fixture(scope="module")
def rag():
    if not NS.startswith("IVG460FX"):
        pytest.fail(f"refusing {NS}: R173's namespace is an IVG460FX* scratch namespace")
    import make_scratch_namespace

    from iris_vector_graph import IRISGraphEngine

    make_scratch_namespace.recreate(NS)
    conn = ns262.connect(NS)
    status = IRISGraphEngine(conn, embedding_dimension=8, namespace=NS, schema_prefix="RAG").initialize_schema()
    assert (status["schema"], ns262.native(conn).get("^IVG.Deploy", "schema")) == ("RAG", "RAG")
    yield conn
    conn.close()


def _graph_kg_engine(conn, dim=8):
    from iris_vector_graph import IRISGraphEngine

    return IRISGraphEngine(conn, embedding_dimension=dim, namespace=NS, native_cypher=False)


def test_the_fixture_is_a_rag_namespace_with_no_graph_kg_objects(rag):
    snap = snapshot(rag)
    assert snap["record"] == "RAG"
    assert not [t for t in snap["tables"] if t[0].upper() == "GRAPH_KG"]
    assert not [r for r in snap["routines"] if r[0].upper() == "GRAPH_KG"]
    assert not {"Graph.KG.SqlProcs", "Graph.KG.LedgerRevision", "Graph.KG.LedgerStats"} & set(snap["classes"])
    assert {"RAG.SqlProcs", "RAG.LedgerRevision", "RAG.LedgerStats"} <= set(snap["classes"])


@pytest.mark.parametrize("dim, dry_run", [(8, False), (8, True), (None, False)])
def test_initialize_schema_refuses_and_changes_nothing(rag, dim, dry_run):
    from iris_vector_graph import IVGSchemaError

    before = snapshot(rag)
    with pytest.raises(IVGSchemaError) as raised:
        _graph_kg_engine(rag, dim).initialize_schema(dry_run=dry_run)
    assert (raised.value.recorded, raised.value.requested) == ("RAG", "Graph_KG")
    assert _diff(before, snapshot(rag)) == {}


def test_deploy_packaged_classes_refuses_and_changes_nothing(rag):
    from iris_vector_graph import IVGSchemaError
    from iris_vector_graph._engine.class_deploy import deploy_packaged_classes

    before = snapshot(rag)
    with pytest.raises(IVGSchemaError):
        deploy_packaged_classes(rag, force=True)  # the Graph_KG default schema
    assert _diff(before, snapshot(rag)) == {}


def test_deploy_objectscript_classes_refuses_and_changes_nothing(rag):
    from iris_vector_graph import IVGSchemaError
    from iris_vector_graph.schema import GraphSchema

    before = snapshot(rag)
    cur = rag.cursor()
    try:
        with pytest.raises(IVGSchemaError):
            GraphSchema.deploy_objectscript_classes(cur, REPO / "iris_src", conn=rag, force=True)
    finally:
        cur.close()
    assert _diff(before, snapshot(rag)) == {}


def test_ensure_indexes_on_a_graph_kg_cursor_changes_nothing(rag):
    """`GraphSchema.ensure_indexes` takes a cursor and nothing else, so it cannot read the record. Its statements name
    `Graph_KG`'s tables, which this namespace does not have: measured here, it creates nothing."""
    from iris_vector_graph.schema import GraphSchema

    before = snapshot(rag)
    cur = rag.cursor()
    try:
        GraphSchema.ensure_indexes(cur)
        rag.commit()
    finally:
        cur.close()
    assert _diff(before, snapshot(rag)) == {}


def test_tcp_deploy_refuses_and_changes_nothing(rag):
    before = snapshot(rag)
    env = dict(os.environ, IVG_NAMESPACE=NS, IVG_PORT=str(ns262.PORT), IVG_ARNO_CONTAINER=ns262.CONTAINER,
               PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))
    out = subprocess.run([str(REPO / "scripts" / "enterprise-container.sh"), "tcp-deploy"], cwd=str(REPO), env=env,
                         capture_output=True, text=True, timeout=600)
    assert out.returncode != 0, out.stdout[-2000:]
    assert f"{NS} records RAG as its IVG Schema" in out.stderr, out.stderr[-2000:]
    assert _diff(before, snapshot(rag)) == {}


def test_the_test_sessions_class_load_refuses_and_changes_nothing(rag):
    """tests/conftest.py's `_deploy_objectscript` (what every pytest session that uses `iris_connection` runs first)."""
    from iris_vector_graph import IVGSchemaError
    import tests.conftest as session

    before = snapshot(rag)
    with pytest.raises(IVGSchemaError):
        session._deploy_objectscript(ns262.CONTAINER, NS)
    assert _diff(before, snapshot(rag)) == {}


def test_a_rag_engine_still_sets_up_the_namespace(rag):
    """The refusals are for the other schema only: the recorded one's setup runs (and is idempotent)."""
    from iris_vector_graph import IRISGraphEngine

    status = IRISGraphEngine(rag, embedding_dimension=8, namespace=NS, schema_prefix="RAG").initialize_schema()
    assert (status["schema"], status["schema_recorded"]) == ("RAG", "existing")
