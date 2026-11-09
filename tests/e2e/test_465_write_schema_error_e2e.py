"""4.6.5: on a namespace whose classes predate 4.6.4, a write inside the caller's transaction raises an exception that
is an ``IVGSchemaError`` (docs/USER_GUIDE.md, CHANGELOG v4.6.4) and a ``GraphWriteError`` (4.6.4's actual class).

The namespace is `IVG_R465_LEGACY_NS` (an IVG463* scratch namespace this file owns). 4.6.4 added the SQL function
`Txn_Begin` (Graph.KG.SqlProcs.TxnBegin); a namespace whose classes predate it has none, and one is made here by
deleting that method and recompiling the class. The classes are put back from this checkout when the file is done.

Measured on 4.6.4 in that namespace, `create_edge` after `START TRANSACTION`: `GraphWriteError`, "create_edge failed,
nothing written: Graph_KG.Txn_Begin is not in this namespace (its IVG classes predate 4.6.4), so this write cannot join
the caller's open transaction without committing it; nothing was written. Run initialize_schema() to bring the classes
up to date.", and not an `IVGSchemaError`.

    IVG_R465_LEGACY_NS=IVG463WB pytest tests/e2e/test_465_write_schema_error_e2e.py
"""

from __future__ import annotations

import os
import uuid

import pytest

from tests.e2e import ns262, scratch_vars
from iris_vector_graph.errors import GraphWriteError
from iris_vector_graph.schema_name import IVGSchemaError

NS = os.environ.get("IVG_R465_LEGACY_NS", "")
CALLER_TABLE = "SQLUser.ivg465_caller"

pytestmark = [pytest.mark.e2e, ns262.requires_container, scratch_vars.requires("IVG_R465_LEGACY_NS")]

WRITES = {
    "create_node": lambda eng, u: eng.create_node(f"{u}n"),
    "create_edge": lambda eng, u: eng.create_edge(f"{u}a", "R", f"{u}b"),
    "delete_node": lambda eng, u: eng.delete_node(f"{u}a"),
    "delete_edge": lambda eng, u: eng.delete_edge(f"{u}a", "R", f"{u}b"),
    "set_edge_weight": lambda eng, u: eng.set_edge_weight(f"{u}a", "R", f"{u}b", 2.0),
    "cypher_create": lambda eng, u: eng.execute_cypher(f"CREATE (:T {{node_id: '{u}c'}})", raise_on_error=True),
}


def _connect():
    return ns262.connect(NS)


def _engine(conn):
    from iris_vector_graph import IRISGraphEngine

    return IRISGraphEngine(conn, namespace=NS)


@pytest.fixture(scope="module")
def legacy():
    """The namespace as 4.6.3 left it: no Txn_Begin. Put right again afterwards."""
    import iris
    import make_scratch_namespace  # scripts/dev, on the path through ns262

    make_scratch_namespace.recreate(NS)
    make_scratch_namespace.install_ivg(NS)
    conn = _connect()
    host = iris.createIRIS(conn)
    host.classMethodValue("%Dictionary.MethodDefinition", "%DeleteId", "Graph.KG.SqlProcs||TxnBegin")
    host.classMethodValue("%SYSTEM.OBJ", "Compile", "Graph.KG.SqlProcs", "ck")
    cur = conn.cursor()
    with pytest.raises(Exception, match="TXN_BEGIN"):
        cur.execute("SELECT Graph_KG.Txn_Begin(0)")
    cur.execute(f"CREATE TABLE {CALLER_TABLE} (k VARCHAR(80) PRIMARY KEY)")
    yield conn
    conn.close()
    make_scratch_namespace.install_ivg(NS)


def _uid():
    return "w" + uuid.uuid4().hex[:10]


def _count(conn, sql, *params):
    cur = conn.cursor()
    cur.execute(sql, list(params))
    return cur.fetchone()[0]


@pytest.fixture
def seeded(legacy):
    """Two nodes and an edge between them, written outside any caller transaction (which this namespace allows)."""
    u = _uid()
    eng = _engine(legacy)
    eng.create_node(f"{u}a")
    eng.create_node(f"{u}b")
    eng.create_edge(f"{u}a", "R", f"{u}b")
    return u


def _open(conn, how):
    if how == "sql":
        conn.cursor().execute("START TRANSACTION")
    else:
        conn.setAutoCommit(False)


def _close(conn, how):
    if how == "sql":
        conn.cursor().execute("COMMIT")
    else:
        conn.commit()
        conn.setAutoCommit(True)


@pytest.mark.parametrize("how", ["sql", "driver"])
@pytest.mark.parametrize("name", list(WRITES))
def test_a_write_in_the_callers_transaction_raises_both_classes_and_writes_nothing(legacy, seeded, name, how):
    u = seeded
    eng = _engine(legacy)
    observer = _connect()
    try:
        before = (
            _count(observer, "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id LIKE ?", f"{u}%"),
            _count(observer, "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s = ?", f"{u}a"),
        )
        caller_key = f"{u}-caller"
        _open(legacy, how)
        try:
            legacy.cursor().execute(f"INSERT INTO {CALLER_TABLE} (k) VALUES (?)", [caller_key])
            with pytest.raises(IVGSchemaError) as caught:
                WRITES[name](eng, u)
        finally:
            _close(legacy, how)
        err = caught.value
        # the class a method's write has raised since 4.6.0, so code catching it keeps working (a Cypher write raised
        # the IVGSchemaError itself, and still does)
        assert isinstance(err, GraphWriteError) or name == "cypher_create"
        assert "Txn_Begin is not in this namespace" in str(err) and "nothing was written" in str(err), str(err)
        assert "initialize_schema" in str(err)
        # the caller's own row committed with the caller; IVG's write left nothing
        assert _count(observer, f"SELECT COUNT(*) FROM {CALLER_TABLE} WHERE k = ?", caller_key) == 1
        after = (
            _count(observer, "SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id LIKE ?", f"{u}%"),
            _count(observer, "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s = ?", f"{u}a"),
        )
        assert after == before, f"{name} changed the graph: {before} -> {after}"
    finally:
        observer.close()


def test_the_documented_except_clause_catches_a_write_in_the_callers_transaction(legacy):
    """docs/USER_GUIDE.md: `except IVGSchemaError` is how a caller on an old namespace is told to run
    initialize_schema()."""
    u = _uid()
    eng = _engine(legacy)
    _open(legacy, "sql")
    try:
        try:
            eng.create_edge(f"{u}x", "R", f"{u}y")
        except IVGSchemaError as exc:
            told = str(exc)
        else:
            pytest.fail("the write was not refused")
    finally:
        _close(legacy, "sql")
    assert "initialize_schema()" in told


def test_without_a_callers_transaction_the_write_still_runs_its_own(legacy):
    u = _uid()
    eng = _engine(legacy)
    assert eng.create_edge(f"{u}x", "R", f"{u}y") is True
    assert _count(legacy, "SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE s = ? AND o_id = ?", f"{u}x", f"{u}y") == 1
