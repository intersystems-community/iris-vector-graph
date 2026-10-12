"""Native-handle sites in the engine and the store go through the Host (package host-sites-2).

Spec: specs/238-alt-host-sites-2/spec.md. The factory tests pin which connection the native handle is built on, as
before, and that the test-only ``_safe_createIRIS`` name check is gone. The static test pins that no migrated file
calls a Native API method on a raw handle. No IRIS is needed.
"""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import pytest

from iris_vector_graph import host as H
from iris_vector_graph.embedded import EmbeddedConnection
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

REPO = Path(__file__).resolve().parents[2]


class RawHandle:
    """Stands in for the object ``iris.createIRIS(conn)`` returns."""


class DedicatedConnection:
    """Stands in for the dedicated ``iris.dbapi.connect`` connection: open until closed."""

    def __init__(self, number):
        self.number = number

    def isClosed(self):
        return False


@pytest.fixture
def fake_iris(monkeypatch):
    """A fake ``iris`` package. ``createIRIS`` records the connection it was handed and returns a fresh handle."""
    calls = []
    dbapi_calls = []
    state = {"connect": None}

    def create_iris(conn):
        calls.append(conn)
        return RawHandle()

    def connect(**kwargs):
        dbapi_calls.append(kwargs)
        if state["connect"] is not None:
            return state["connect"]()
        return DedicatedConnection(len(dbapi_calls))

    dbapi = types.ModuleType("iris.dbapi")
    dbapi.connect = connect
    module = types.ModuleType("iris")
    module.createIRIS = create_iris
    module.dbapi = dbapi
    monkeypatch.setitem(sys.modules, "iris", module)
    monkeypatch.setitem(sys.modules, "iris.dbapi", dbapi)
    return types.SimpleNamespace(
        calls=calls, dbapi_calls=dbapi_calls, state=state, module=module
    )


PARAMS = {
    "hostname": "h",
    "port": 1972,
    "namespace": "USER",
    "username": "u",
    "password": "p",
}


def _engine(conn, params=None, native_conn=None):
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng.conn = conn
    eng._connection_params = params
    eng._native_conn = native_conn
    return eng


def _store(conn):
    st = IRISGraphStore.__new__(IRISGraphStore)
    st.conn = conn
    return st


# ----------------------------------------------------------------- engine: which connection (US2, FR-001, FR-004)


def test_engine_without_stored_params_builds_the_handle_on_the_callers_connection(
    fake_iris,
):
    conn = object()
    eng = _engine(conn)

    host = eng._host()

    assert isinstance(host, H.ClientHost)
    assert fake_iris.calls == [conn]
    assert fake_iris.dbapi_calls == []


def test_engine_opens_a_dedicated_connection_and_reuses_it(fake_iris):
    conn = object()
    eng = _engine(conn, params=dict(PARAMS))

    eng._host()
    eng._host()

    assert len(fake_iris.dbapi_calls) == 1, "the dedicated connection is opened once"
    assert fake_iris.dbapi_calls[0]["username"] == "u"
    assert fake_iris.calls[0] is eng._native_conn
    assert fake_iris.calls[0] is not conn, "the caller's connection is not used for native calls"


def test_engine_falls_back_to_the_callers_connection_when_the_dedicated_one_fails(
    fake_iris,
):
    conn = object()
    fake_iris.state["connect"] = lambda: (_ for _ in ()).throw(RuntimeError("no"))
    eng = _engine(conn, params=dict(PARAMS))

    eng._host()

    assert fake_iris.calls == [conn]


def test_engine_on_an_embedded_connection_gets_the_in_process_host(fake_iris):
    eng = _engine(EmbeddedConnection())

    host = eng._host()

    assert isinstance(host, H.InProcessHost)
    assert fake_iris.calls == [], "no Native API connection is opened inside IRIS"


def test_a_createiris_failure_surfaces_when_the_host_is_built(fake_iris):
    def broken(conn):
        raise RuntimeError("createIRIS refused")

    fake_iris.module.createIRIS = broken
    eng = _engine(object())

    with pytest.raises(RuntimeError, match="createIRIS refused"):
        eng._host()


def test_the_raw_accessor_still_returns_the_handle_createiris_made(fake_iris):
    conn = object()
    eng = _engine(conn)

    raw = eng._iris_obj()

    assert isinstance(raw, RawHandle)
    assert fake_iris.calls == [conn]


def test_the_test_only_name_check_is_gone(fake_iris):
    """A patched createIRIS named ``_safe_createIRIS`` does not switch the dedicated connection off any more."""
    conn = object()

    def _safe_createIRIS(target):
        fake_iris.calls.append(target)
        return RawHandle()

    fake_iris.module.createIRIS = _safe_createIRIS
    eng = _engine(conn, params=dict(PARAMS))

    eng._host()

    assert fake_iris.calls and fake_iris.calls[0] is eng._native_conn
    assert fake_iris.calls[0] is not conn


# ------------------------------------------------------------------ store: the same factory, no dedicated link


def test_store_builds_the_handle_on_its_connection(fake_iris):
    conn = object()
    st = _store(conn)

    host = st._host()

    assert isinstance(host, H.ClientHost)
    assert fake_iris.calls == [conn]


def test_store_on_an_embedded_connection_gets_the_in_process_host(fake_iris):
    st = _store(EmbeddedConnection())

    assert isinstance(st._host(), H.InProcessHost)
    assert fake_iris.calls == []


def test_store_raw_accessor_still_returns_the_handle(fake_iris):
    conn = object()
    st = _store(conn)

    assert isinstance(st._iris_obj(), RawHandle)
    assert fake_iris.calls == [conn]


# --------------------------------------------------------------------------- static: no raw calls left (SC-003)

# Files this package owns. schema.py belongs to another package; class_deploy.py uses % methods, which the Host
# refuses, so it stays on the raw handle on purpose.
MIGRATED = [
    REPO / "iris_vector_graph" / "engine.py",
    REPO / "iris_vector_graph" / "stores" / "iris_sql_store.py",
] + sorted(
    p
    for p in (REPO / "iris_vector_graph" / "_engine").glob("*.py")
    if p.name not in ("schema.py", "class_deploy.py")
)

RAW_METHODS = re.compile(
    r"\.(classMethodValue|classMethodVoid|classMethodObject|isDefined|nextSubscript)\("
)
# The Host has no void call (classMethodVoid); a void call is a classMethodValue that errors on IRIS ("must return a
# value"). These lines stay on the raw handle until the Host gains one. See the report: NEEDS FROM THE LEAD.
VOID_KEPT_RAW = re.compile(
    r"self\._iris_obj\(\)\.classMethodVoid\(|iris_obj\.classMethodVoid\(\"Graph\.KG\.ArnoAccel\", \"BumpVersion\"\)"
)


@pytest.mark.parametrize("path", MIGRATED, ids=lambda p: p.name)
def test_no_native_method_name_is_left_in_a_migrated_file(path):
    text = path.read_text()
    hits = [
        f"{n}: {line.strip()}"
        for n, line in enumerate(text.splitlines(), 1)
        if RAW_METHODS.search(line) and not VOID_KEPT_RAW.search(line)
    ]
    assert hits == []


@pytest.mark.parametrize("path", MIGRATED, ids=lambda p: p.name)
def test_no_migrated_file_calls_the_raw_accessor_or_createiris(path):
    text = path.read_text()
    hits = [
        f"{n}: {line.strip()}"
        for n, line in enumerate(text.splitlines(), 1)
        if re.search(r"_iris_obj\(\)|createIRIS\(", line)
        and not line.lstrip().startswith("#")
        and "def _iris_obj" not in line
        and not VOID_KEPT_RAW.search(line)
        and not (path.name == "snapshot.py" and "_raise_" in line and "self._iris_obj()" in line)
    ]
    # engine.py and the store keep one raw accessor each, which calls createIRIS to build the native handle.
    allowed = {
        REPO / "iris_vector_graph" / "engine.py",
        REPO / "iris_vector_graph" / "stores" / "iris_sql_store.py",
    }
    if path in allowed:
        hits = [h for h in hits if "createIRIS(" not in h]
    assert hits == []
