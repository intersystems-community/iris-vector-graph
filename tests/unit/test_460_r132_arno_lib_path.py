"""4.6.0 R132: both Arno loaders resolve the library path one way, and `IVG_ARNO_LIB` reaches both.

Before: `Graph.KG.ArnoAccel.Load` defaulted to `/tmp/libarno_callout.so`, `Graph.KG.NKGAccel.Load` (the BFS loader) to
`/usr/irissys/mgr/libarno_callout.so`, and the engine called both with no argument, so `IVG_ARNO_LIB` reached neither
loader and one process could hold two different builds (the test container has a pinned build at /tmp and an older one
at /usr/irissys/mgr). Now one order, `Graph.KG.ArnoAccel.LibPath(pPath)`:

1. the argument, which the engine passes from `IVG_ARNO_LIB`;
2. `^ArnoAccel("lib_path")`, recorded by the last successful load in the namespace;
3. `/usr/irissys/mgr/libarno_callout.so` (ADMIN_GUIDE's documented default). Nothing is loaded from /tmp by default.

The ObjectScript half is read from the class sources here and run live in
tests/e2e/test_460_r132_arno_lib_path_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.stores import arno_bridge
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

KG = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG"
DEFAULT = "/usr/irissys/mgr/libarno_callout.so"


def _method(cls_file: str, name: str) -> str:
    src = (KG / cls_file).read_text()
    start = src.index(f"ClassMethod {name}(")
    end = src.find("\nClassMethod ", start + 1)
    return src[start:end if end > 0 else None]


# --------------------------------------------------------------------------- the server's one order


def test_libpath_is_the_one_resolver_with_the_documented_default():
    src = (KG / "ArnoAccel.cls").read_text()
    assert f'Parameter DEFAULTLIBPATH = "{DEFAULT}";' in src
    body = _method("ArnoAccel.cls", "LibPath")
    assert body.startswith('ClassMethod LibPath(pPath As %String = "") As %String')
    arg, recorded, default = (body.index(t) for t in ('pPath', '^ArnoAccel("lib_path")', "..#DEFAULTLIBPATH"))
    assert arg < recorded < default


@pytest.mark.parametrize("cls_file", ["ArnoAccel.cls", "NKGAccelLoader.cls", "NKGAccel.cls"])
def test_every_loader_defaults_to_no_path_and_resolves_through_libpath(cls_file):
    body = _method(cls_file, "Load")
    assert re.match(r'ClassMethod Load\(\w+ As %String = ""\) As %Boolean', body), body.splitlines()[0]
    if cls_file != "NKGAccel.cls":  # NKGAccel.Load forwards to NKGAccelLoader.Load
        assert "LibPath(" in body


def test_no_class_loads_the_library_from_tmp_by_default():
    hits = [f"{p.name}:{i}" for p in KG.glob("*.cls") for i, line in enumerate(p.read_text().splitlines(), 1)
            if "/tmp/libarno" in line and not line.lstrip().startswith("//")]
    assert hits == []


def test_the_bfs_path_and_build2hop_resolve_through_libpath():
    bfs = _method("NKGAccelTraversal.cls", "BFSJson")
    assert "/usr/irissys/mgr" not in bfs and "LibPath()" in bfs
    exact = _method("NKGAccelTraversal.cls", "Build2HopExact")
    assert 'NKGAccelLoader).Load()' in exact


def test_each_loader_says_which_file_it_loaded():
    assert "^||ArnoAccel(\"path\")" in _method("ArnoAccel.cls", "LoadedPath")
    assert "^||NKGAccel(\"libPath\")" in _method("NKGAccelLoader.cls", "LoadedPath")


# --------------------------------------------------------------------------- the engine passes IVG_ARNO_LIB to both


def _store():
    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = MagicMock()
    store._arno_available = None
    store._arno_capabilities = {}
    store._nkg_dirty = False
    return store


def _native():
    calls = []

    def value(cls, method, *args):
        calls.append((cls, method, args))
        return {"IsAvailable": 1, "Load": 1, "Capabilities": '{"rust_callout":true}', "GetFirstNKGNode": "",
                "IsLoaded": 1, "GetLibPath": "/recorded/libarno.so"}.get(method, "")

    obj = MagicMock()
    obj.classMethodValue.side_effect = value
    return obj, calls


def _detect(monkeypatch, env):
    if env is None:
        monkeypatch.delenv("IVG_ARNO_LIB", raising=False)
    else:
        monkeypatch.setenv("IVG_ARNO_LIB", env)
    monkeypatch.delenv("IVG_DISABLE_ARNO", raising=False)
    store = _store()
    obj, calls = _native()
    with patch.object(store, "_iris_obj", return_value=obj), patch.object(store, "_check_namespace"), \
            patch("iris_vector_graph.stores.iris_sql_store.compiled_class_exists", return_value=True):
        store._detect_arno()
    return [(c, a) for c, m, a in calls if m == "Load"]


def test_ivg_arno_lib_reaches_both_loaders(monkeypatch):
    loads = _detect(monkeypatch, "/opt/arno/libarno_callout.so")
    assert ("Graph.KG.ArnoAccel", ("/opt/arno/libarno_callout.so",)) in loads
    assert ("Graph.KG.NKGAccel", ("/opt/arno/libarno_callout.so",)) in loads


def test_without_ivg_arno_lib_both_loaders_resolve_on_the_server(monkeypatch):
    loads = _detect(monkeypatch, None)
    assert loads and all(args == () for _cls, args in loads), loads
    assert {c for c, _ in loads} >= {"Graph.KG.ArnoAccel", "Graph.KG.NKGAccel"}


def test_a_blank_ivg_arno_lib_is_unset(monkeypatch):
    loads = _detect(monkeypatch, "  ")
    assert loads and all(args == () for _cls, args in loads)


def test_a_reload_uses_ivg_arno_lib_first(monkeypatch):
    monkeypatch.setenv("IVG_ARNO_LIB", "/opt/arno/libarno_callout.so")
    store = _store()
    obj, calls = _native()
    obj.classMethodValue.side_effect = lambda cls, m, *a: (calls.append((cls, m, a)), 0 if m == "IsAvailable" else 1)[1]
    assert store._reload_arno_if_needed(obj) is True
    assert ("Graph.KG.ArnoAccel", "Load", ("/opt/arno/libarno_callout.so",)) in calls


def test_a_reload_without_ivg_arno_lib_lets_the_server_resolve(monkeypatch):
    monkeypatch.delenv("IVG_ARNO_LIB", raising=False)
    store = _store()
    obj, calls = _native()
    obj.classMethodValue.side_effect = lambda cls, m, *a: (calls.append((cls, m, a)), 0 if m == "IsAvailable" else 1)[1]
    store._reload_arno_if_needed(obj)
    assert ("Graph.KG.ArnoAccel", "Load", ()) in calls


# --------------------------------------------------------------------------- the SQL-function path (arno_bridge)


def test_the_bridge_probes_ivg_arno_lib_first(monkeypatch):
    monkeypatch.setenv("IVG_ARNO_LIB", "/opt/arno/libarno_callout.so")
    host = MagicMock()
    with patch.object(arno_bridge, "host_for", return_value=host):
        assert arno_bridge.resolve_lib_path(MagicMock()) == "/opt/arno/libarno_callout.so"
    host.call.assert_not_called()


def test_the_bridge_asks_the_server_when_ivg_arno_lib_is_unset(monkeypatch):
    monkeypatch.delenv("IVG_ARNO_LIB", raising=False)
    host = MagicMock()
    host.method_exists.return_value = True
    host.call.return_value = "/recorded/libarno.so"
    with patch.object(arno_bridge, "host_for", return_value=host):
        assert arno_bridge.resolve_lib_path(MagicMock()) == "/recorded/libarno.so"
    host.call.assert_called_once_with("Graph.KG.ArnoAccel", "LibPath")


def test_the_bridge_falls_back_to_the_default_where_the_class_is_not_deployed(monkeypatch):
    monkeypatch.delenv("IVG_ARNO_LIB", raising=False)
    host = MagicMock()
    host.method_exists.return_value = False
    with patch.object(arno_bridge, "host_for", return_value=host):
        assert arno_bridge.resolve_lib_path(MagicMock()) == DEFAULT
    host.call.assert_not_called()


# --------------------------------------------------------------------------- what an operator sees


def _report_lines(arno):
    from iris_vector_graph.status import EngineStatus

    return EngineStatus(arno=arno).report().splitlines()


def test_the_report_names_the_library_the_engine_loaded():
    from iris_vector_graph.status import ArnoStatus

    lines = _report_lines(ArnoStatus(loaded=True, version="arno-callout 1.2.1+1123cb8", lib_path=DEFAULT))
    assert f"Arno library:    {DEFAULT} (loaded)" in lines


def test_the_report_names_the_path_it_tried_when_nothing_loaded():
    from iris_vector_graph.status import ArnoStatus

    lines = _report_lines(ArnoStatus(loaded=False, lib_path=DEFAULT))
    assert "Acceleration:    ObjectScript (Arno not loaded)" in lines
    assert f"Arno library:    {DEFAULT} (not loaded)" in lines


def test_no_library_line_where_no_path_was_resolved():
    from iris_vector_graph.status import ArnoStatus

    assert not [line for line in _report_lines(ArnoStatus(loaded=False)) if line.startswith("Arno library:")]


def test_status_reads_the_loaded_path_and_else_the_resolved_one(monkeypatch):
    from iris_vector_graph._engine.admin import arno_library_path

    monkeypatch.setenv("IVG_ARNO_LIB", "/opt/arno/libarno_callout.so")
    native = MagicMock()
    answers = {"LoadedPath": "/tmp/libarno_tcp_1.so"}
    native.classMethodValue.side_effect = lambda cls, m, *a: answers[m] if m in answers else (a[0] if a else DEFAULT)
    assert arno_library_path(native, loaded=True) == "/tmp/libarno_tcp_1.so"
    assert arno_library_path(native, loaded=False) == "/opt/arno/libarno_callout.so"
    monkeypatch.delenv("IVG_ARNO_LIB")
    assert arno_library_path(native, loaded=False) == DEFAULT
