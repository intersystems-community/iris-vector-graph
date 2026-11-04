"""Spec 262 T044: an engine's first database access checks the namespace's IVG Schema record (FR-006, FR-006a;
contracts/python-api.md, "First database access").

The connection is a mock that claims to be an IRIS connection (``spec=iris.IRISConnection``): the check runs only for
one, and a plain MagicMock connection (the rest of the unit suite) is not one. The engine reaches the Native API through
``IRISGraphEngine._iris_obj``, which is what is patched here; the fake native answers the record and the class probe
and nothing else.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import iris
import pytest

from iris_vector_graph import IRISGraphEngine, IVGSchemaError

MARKER_GRAPH_KG = "Graph.KG.funcCYCMP"


class FakeNative:
    """``get`` over a dict, ``isDefined("^oddDEF", class)`` over a set, ``%SYSTEM.SYS.NameSpace``; each call
    recorded."""

    def __init__(self, record=None, classes=(), namespace="IVG262X"):
        self.data = {} if record is None else {("^IVG.Deploy", "schema"): record}
        self.classes = set(classes)
        self.namespace = namespace
        self.calls = []

    def get(self, gname, *subs):
        self.calls.append(("get", gname, *subs))
        return self.data.get((gname, *subs))

    def isDefined(self, gname, *subs):
        self.calls.append(("isDefined", gname, *subs))
        if gname == "^oddDEF" and len(subs) == 1:
            return 10 if subs[0] in self.classes else 0
        raise AssertionError(f"unexpected isDefined {gname}{subs}")

    def classMethodValue(self, cls, method, *args):
        self.calls.append((cls, method, *args))
        if (cls, method) == ("%SYSTEM.SYS", "NameSpace"):
            return self.namespace
        raise AssertionError(f"unexpected native call {cls}.{method}{args}")


def _conn():
    conn = MagicMock(spec=iris.IRISConnection)
    cursor = MagicMock()
    cursor.fetchone.return_value = (5,)
    conn.cursor.return_value = cursor
    return conn


def _engine(conn, native, schema=None, refused=True):
    """Built with the native patched. A refused engine sends no SQL at construction either (its probes wait for
    setup); an accepted one runs its construction probes as before, so callers count from after construction."""
    with patch.object(IRISGraphEngine, "_iris_obj", return_value=native):
        eng = IRISGraphEngine(conn, embedding_dimension=4, vector_dtype="DOUBLE", schema_prefix=schema,
                              native_cypher=False)
    if refused:
        assert conn.cursor.call_count == 0, "a refused engine's construction sends no SQL"
    conn.cursor.reset_mock()
    return eng


@pytest.fixture
def native_patch():
    patches = []

    def apply(native):
        p = patch.object(IRISGraphEngine, "_iris_obj", return_value=native)
        p.start()
        patches.append(p)
        return native

    yield apply
    for p in patches:
        p.stop()


def test_a_mismatched_engine_is_refused_before_any_sql(native_patch):
    conn = _conn()
    native = native_patch(FakeNative(record="Graph_KG"))
    eng = _engine(conn, native, schema="RAG")
    with pytest.raises(IVGSchemaError) as raised:
        eng.execute_cypher("MATCH (n) RETURN count(n)")
    text = str(raised.value)
    for part in ("Graph_KG", "RAG", "one IVG Schema per namespace", "use a separate namespace", "IVG262X"):
        assert part in text, text
    assert raised.value.recorded == "Graph_KG" and raised.value.requested == "RAG"
    assert conn.cursor.call_count == 0


@pytest.mark.parametrize("call", [
    lambda e: e.get_node_count(),
    lambda e: e.create_node("n1", labels=["L"]),
    lambda e: e._store.get_nodes(["n1"]),
])
def test_every_kind_of_first_access_is_refused(native_patch, call):
    conn = _conn()
    native = native_patch(FakeNative(record="Graph_KG"))
    eng = _engine(conn, native, schema="RAG")
    with pytest.raises(IVGSchemaError):
        call(eng)
    assert conn.cursor.call_count == 0


def test_a_refused_engine_is_refused_again(native_patch):
    conn = _conn()
    native = native_patch(FakeNative(record="Graph_KG"))
    eng = _engine(conn, native, schema="RAG")
    for _ in range(2):
        with pytest.raises(IVGSchemaError):
            eng.get_node_count()
    assert conn.cursor.call_count == 0


def test_no_record_and_no_46_objects_is_the_not_upgraded_error(native_patch):
    conn = _conn()
    native = native_patch(FakeNative(record=None, classes={"User.funcCYCMP"}))
    eng = _engine(conn, native)
    with pytest.raises(IVGSchemaError) as raised:
        eng.execute_cypher("MATCH (n) RETURN count(n)")
    text = str(raised.value)
    for part in ("set up by an earlier IVG release", "initialize_schema()", "initialize_schema(dry_run=True)",
                 "IVG262X"):
        assert part in text, text
    assert ("isDefined", "^oddDEF", MARKER_GRAPH_KG) in native.calls
    assert conn.cursor.call_count == 0


def test_the_objects_probe_follows_the_engines_schema(native_patch):
    conn = _conn()
    native = native_patch(FakeNative(record=None, classes={"IVGALT.funcCYCMP"}))
    eng = _engine(conn, native, schema="IVGALT", refused=False)
    assert eng.get_node_count() == 5
    assert ("isDefined", "^oddDEF", "IVGALT.funcCYCMP") in native.calls


def test_a_matching_record_costs_one_round_trip_then_none(native_patch):
    conn = _conn()
    native = native_patch(FakeNative(record="ivgalt"))  # recorded in another case: one schema all the same
    eng = _engine(conn, native, schema="IVGALT", refused=False)
    assert eng.get_node_count() == 5
    assert native.calls == [("get", "^IVG.Deploy", "schema")]
    assert eng.get_node_count() == 5
    eng._store.conn  # the store shares the engine's check
    assert native.calls == [("get", "^IVG.Deploy", "schema")]
    assert conn.cursor.call_count == 2


def test_a_connection_that_is_not_iris_is_not_checked(native_patch):
    conn = MagicMock()  # not an IRIS connection: no Native API to read a record with
    conn.cursor.return_value.fetchone.return_value = (5,)
    native = native_patch(FakeNative(record="Graph_KG"))
    eng = IRISGraphEngine(conn, embedding_dimension=4, vector_dtype="DOUBLE", schema_prefix="RAG")
    assert eng.get_node_count() == 5
    assert native.calls == []


def test_initialize_schema_dry_run_is_not_blocked_on_a_not_upgraded_namespace(native_patch):
    """The way out of the FR-006a error must not be refused by it: the dry run reaches its own reads."""
    conn = _conn()
    native = native_patch(FakeNative(record=None))
    eng = _engine(conn, native)
    with patch("iris_vector_graph._engine.legacy.present_legacy_objects", return_value=[]), \
            patch.object(IRISGraphEngine, "_ivg_table_schemas", return_value={}, create=False):
        out = eng.initialize_schema(dry_run=True)
    assert out["dry_run"] is True and out["schema"] == "Graph_KG" and out["schema_recorded"] == "new"


def test_a_method_that_catches_every_exception_still_raises_the_refusal(native_patch):
    """The store's temporal write answers False on any exception (write_temporal_edge); the refusal is raised at the
    method's entry instead, so it cannot become a silent False (red: `DID NOT RAISE`, the E2E temporal_write case)."""
    conn = _conn()
    native = native_patch(FakeNative(record="Graph_KG"))
    eng = _engine(conn, native, schema="RAG")
    with pytest.raises(IVGSchemaError):
        eng.create_edge_temporal("a", "R", "b", 1_700_000_000)
    with pytest.raises(IVGSchemaError):
        eng.temporal.create_edge_temporal("a", "R", "b", 1_700_000_000)
    with pytest.raises(IVGSchemaError):
        eng._store.write_temporal_edge("a", "R", "b", 1_700_000_000)
    assert conn.cursor.call_count == 0


def test_an_accepted_engine_carries_no_method_wrappers(native_patch):
    conn = _conn()
    native = native_patch(FakeNative(record="Graph_KG"))
    eng = _engine(conn, native, refused=False)
    assert "create_node" not in vars(eng) and "write_temporal_edge" not in vars(eng._store)


def test_setup_releases_the_wrappers(native_patch):
    conn = _conn()
    native = native_patch(FakeNative(record=None))
    eng = _engine(conn, native)
    assert "create_node" in vars(eng)
    with patch.object(IRISGraphEngine, "_record_and_initialize", return_value={"schema": "Graph_KG"}), \
            patch.object(IRISGraphEngine, "_build_index_registry", return_value={}):
        eng.initialize_schema()
    assert "create_node" not in vars(eng) and "write_temporal_edge" not in vars(eng._store)
    assert eng.get_node_count() == 5
