"""4.6.0 R288 [SECURITY]: IVG's SQL functions that reach `$ZF(-4)` / `$ZF(-5)` open only the Arno library R132's
resolver chose and look up only the functions IVG calls.

`ivg_arno_zf_call_2s(libpath, fname, a1, a2)` took both from its caller: anyone holding EXECUTE on it and the callout
resource (%System_CallOut) could load any library on the server and run any function in it (measured 2026-11-04 on
2026.3.0AI: `/tmp/libarno_callout.so`, which is not the resolved library, loaded and answered). Every `ivg_arno_*`
wrapper took the path. Each now calls Graph.KG.ArnoAccel.RequireCallout(path, function) before its first `$ZF`, which
throws SQLCODE -99 naming the path unless it is `LibPath()`, and the function unless it is on the class's allow-list.

The list of wrappers is found, not written: every `CREATE ... FUNCTION` the package's Python creates whose body reaches
`$ZF`. The SQL surface reaches `$ZF` nowhere else: setup's DDL and the SqlProcs forwarders name none, and every IVG
class that looks a function up in the library names it with a literal (the classes are called over the Native API,
whose boundary is the database resource and %Native_ClassExecution, ADMIN_GUIDE section 3). The live check is
tests/e2e/test_460_r288_arno_callout_allowlist_e2e.py.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph._engine import legacy

PKG = Path(legacy.__file__).resolve().parents[1]
CHECK = "##class(Graph.KG.ArnoAccel).RequireCallout("
#: A function the package's Python creates: its name, its parameter list and its ObjectScript body (template text, the
#: schema unfilled).
_DDL = re.compile(r"CREATE OR REPLACE FUNCTION \{schema\}\.(\w+)\((.*?)\)\s*\nRETURNS[^\n]*\nLANGUAGE OBJECTSCRIPT\n"
                  r"\{\{\n(.*?)\n\}\}", re.S)


def _wrappers() -> dict:
    """{name: (parameter names, body)} for every function created from the package's Python whose body reaches $ZF."""
    out = {}
    for path in sorted(PKG.rglob("*.py")):
        for name, params, body in _DDL.findall(path.read_text(encoding="utf-8")):
            if "$ZF(" in body:
                out[name] = ([p.split()[0] for p in params.split(",") if p.strip()], body)
    return out


def _sources() -> dict:
    from iris_vector_graph._engine.class_deploy import packaged_class_dir, read_class_sources

    return read_class_sources(packaged_class_dir())


def test_the_wrappers_found_are_every_arno_wrapper():
    names = set(_wrappers())
    assert {"ivg_arno_zf_call_2s", "ivg_arno_zf_probe", "ivg_arno_zf_triangle_json", "ivg_arno_zf_leiden_run",
            "ivg_arno_zf_get_result_len", "ivg_arno_adj_append", "ivg_arno_build_adj"} <= names
    assert all(n.startswith("ivg_arno_") for n in names), names
    assert len(names) == 18, sorted(names)


@pytest.mark.parametrize("name", sorted(_wrappers()))
def test_each_wrapper_checks_its_path_and_function_before_its_first_zf(name):
    params, body = _wrappers()[name]
    code = legacy._code(body)
    at = code.find(CHECK)
    assert 0 <= at < code.find("$ZF("), (name, code[:300])
    m = re.match(r"(\w+),\s*(\"\w+\"|\w+)\)", code[at + len(CHECK):])
    assert m, code[at:at + 120]
    lib, fn = m.groups()
    assert lib == params[0] and "lib" in lib, (name, lib)  # the library path parameter, not a constant
    looked_up = set(re.findall(r"\$ZF\(-4,3,dllid,(\"\w+\"|\w+)\)", code))
    assert looked_up == {fn}, (name, fn, looked_up)  # the function checked is the function looked up
    assert set(re.findall(r"\$ZF\(-4,1,(\w+)\)", code)) == {lib}, name  # and the library checked is the one loaded


def test_the_wrappers_taking_a_function_name_check_that_name():
    for name in ("ivg_arno_zf_call_2s", "ivg_arno_zf_probe"):
        params, body = _wrappers()[name]
        assert f"{CHECK}{params[0]}, {params[1]})" in body, name


def test_the_allow_list_is_the_functions_the_wrappers_call():
    src = _sources()["Graph.KG.ArnoAccel"]
    m = re.search(r'Parameter SQLCALLOUTS = "([^"]*)";', src)
    assert m, "Graph.KG.ArnoAccel has no SQLCALLOUTS parameter"
    allowed = {f for f in m.group(1).split(",") if f}
    fixed = set()
    for _params, body in _wrappers().values():
        fixed.update(re.findall(r'\$ZF\(-4,3,dllid,"(\w+)"\)', body))
    # the probe asks for "version" (arno_available), the only function IVG names to the two name-taking wrappers
    assert allowed == fixed | {"version"}, (allowed ^ (fixed | {"version"}))
    assert "init" not in allowed and "capabilities" not in allowed


def test_require_callout_compares_with_the_resolved_path_and_throws_minus_99_naming_what_it_refused():
    src = _sources()["Graph.KG.ArnoAccel"]
    body = src[src.index("ClassMethod RequireCallout("):]
    body = legacy._code(body[:body.index("\n}") + 2])
    assert "..LibPath()" in body
    assert body.count("CreateFromSQLCODE(-99,") == 2
    assert '"Arno library "_pLib_" refused' in body and '"Arno function "_pFn_" refused' in body
    assert "..#SQLCALLOUTS" in body


def test_no_other_sql_callable_body_reaches_zf():
    bodies = legacy._routine_bodies()  # setup's DDL and the SqlProcs forwarders with the classes they name
    assert [n for n, b in bodies.items() if "$ZF(" in legacy._code(b)] == []


def test_every_class_names_the_library_function_it_looks_up():
    """A function looked up in the library is a literal, or one of ArnoAccel.Load's literal list: no class method
    passes a caller's argument to `$ZF(-4, 3)`."""
    loose = []
    for cls, src in _sources().items():
        for m in re.finditer(r"\$ZF\(\s*-4\s*,\s*3\s*,\s*[\w(\")^|]+?\s*,\s*([^)]*)\)", legacy._code(src)):
            arg = m.group(1).strip()
            if re.fullmatch(r'"\w+"', arg) or (cls == "Graph.KG.ArnoAccel" and arg == "fn"):
                continue
            loose.append((cls, arg))
    assert loose == []
    load = _sources()["Graph.KG.ArnoAccel"]
    assert re.search(r'For fn = ("\w+",)+"\w+" \{', load)


# --------------------------------------------------------------------------- IVG_ARNO_LIB still reaches the wrappers


def test_a_configured_path_the_server_has_not_recorded_is_loaded_so_the_wrappers_accept_it(monkeypatch):
    from iris_vector_graph.stores import arno_bridge

    monkeypatch.setenv("IVG_ARNO_LIB", "/opt/arno/libarno_callout.so")
    host = MagicMock()
    host.method_exists.return_value = True
    host.call.side_effect = lambda cls, m, *a: {"LibPath": "/usr/irissys/mgr/libarno_callout.so", "Load": 1}[m]
    with patch.object(arno_bridge, "host_for", return_value=host):
        arno_bridge.record_configured_lib_path(MagicMock())
    host.call.assert_any_call("Graph.KG.ArnoAccel", "Load", "/opt/arno/libarno_callout.so")


def test_a_configured_path_the_server_already_resolves_is_not_loaded_again(monkeypatch):
    from iris_vector_graph.stores import arno_bridge

    monkeypatch.setenv("IVG_ARNO_LIB", "/opt/arno/libarno_callout.so")
    host = MagicMock()
    host.method_exists.return_value = True
    host.call.return_value = "/opt/arno/libarno_callout.so"
    with patch.object(arno_bridge, "host_for", return_value=host):
        arno_bridge.record_configured_lib_path(MagicMock())
    host.call.assert_called_once_with("Graph.KG.ArnoAccel", "LibPath")


def test_without_a_configured_path_nothing_is_loaded(monkeypatch):
    from iris_vector_graph.stores import arno_bridge

    monkeypatch.delenv("IVG_ARNO_LIB", raising=False)
    host = MagicMock()
    with patch.object(arno_bridge, "host_for", return_value=host):
        arno_bridge.record_configured_lib_path(MagicMock())
    host.call.assert_not_called()
