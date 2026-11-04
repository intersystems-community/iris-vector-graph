"""4.6.0 R272: ``scripts/enterprise-container.sh tcp-deploy`` skips the classes setup skips, and says so.

On Community (no ``%AI`` package) the three MCP classes (``Graph.KG.MCPService``, ``MCPToolSet``, ``MCPTools``) cannot
compile. ``initialize_schema`` leaves them out and reports why (``class_deploy.select_classes``); ``tcp-deploy`` loaded
every file, counted the three as compile errors and exited 1, so ``make_scratch_namespace.py`` failed on Community
with nothing wrong. It now asks ``select_classes`` the same question with the same probes, does not load a skipped
class, prints it with setup's reason, and exits 0 when everything it loaded compiled. A class that should compile and
does not is still an error.

The script's Python runs here in-process against a stand-in ``iris`` module: the server has embedded Python and no
``%AI.*`` class, and a load of an MCP class fails as Community's compiler does.
"""

from __future__ import annotations

import os
import socket
import sys
import types
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "enterprise-container.sh"
MCP = ("Graph/KG/MCPService.cls", "Graph/KG/MCPToolSet.cls", "Graph/KG/MCPTools.cls")


def _body() -> str:
    text = SCRIPT.read_text()
    return text.split("  tcp-deploy)", 1)[1].split("<< 'PYEOF'\n", 1)[1].split("\nPYEOF\n", 1)[0]


class _Stream:
    def invokeVoid(self, *args):
        pass


class _Iris:
    """A server with embedded Python and no %AI classes; ``broken`` files fail to compile."""

    def __init__(self, ai=False, broken=()):
        self.ai, self.broken, self.loaded = ai, tuple(broken), []

    def get(self, *subs):
        return None

    def classMethodObject(self, cls, method, *args):
        return _Stream()

    def classMethodVoid(self, *args):
        pass

    def classMethodValue(self, cls, method, *args):
        if (cls, method) == ("%SYSTEM.OBJ", "Load"):
            dest = args[0]
            self.loaded.append(dest)
            failing = (not self.ai and dest.endswith(MCP)) or dest.endswith(self.broken)
            return "0 ERROR #5373: Class '%AI.Tool' does not exist" if failing else "1"
        if (cls, method) == ("%SYSTEM.Status", "IsOK"):
            return args[0] == "1"
        if (cls, method) == ("%SYSTEM.Status", "GetOneErrorText"):
            return args[0][2:]
        if (cls, method) == ("%Dictionary.CompiledClass", "%ExistsId"):
            return int(self.ai or not args[0].startswith("%AI."))
        return 1


def _run(monkeypatch, capsys, server, keep_embedded_python=False):
    stub = types.ModuleType("iris")
    stub.connect = lambda *a, **kw: types.SimpleNamespace(close=lambda: None)
    stub.createIRIS = lambda conn: server
    monkeypatch.setitem(sys.modules, "iris", stub)
    monkeypatch.chdir(REPO)
    monkeypatch.setenv("IVG_NAMESPACE", "IVG460FY4")
    monkeypatch.setenv("IVG_ARNO_CONTAINER", "fixes39-no-such-container")

    def _no_dns(host):
        raise OSError(f"no DNS for {host} in this test")

    monkeypatch.setattr(socket, "gethostbyname", _no_dns)  # the OrbStack lookup, else a 5 s resolver timeout
    if not keep_embedded_python:
        monkeypatch.delenv("IVG_EMBEDDED_PYTHON", raising=False)
    code = 0
    try:
        exec(compile(_body(), str(SCRIPT), "exec"), {"__name__": "__main__"})
    except SystemExit as exc:
        code = exc.code or 0
    return code, capsys.readouterr().out


def test_on_community_the_mcp_classes_are_skipped_not_errors(monkeypatch, capsys):
    server = _Iris(ai=False)
    code, out = _run(monkeypatch, capsys, server)
    assert code == 0, out
    assert not any(d.endswith(MCP) for d in server.loaded), "a class setup skips was loaded"
    for cls in ("Graph.KG.MCPService", "Graph.KG.MCPToolSet", "Graph.KG.MCPTools"):
        assert f"skipped {cls}: needs %AI." in out, out
    assert "compile error" not in out and "ERROR" not in out, out
    assert "3 skipped" in out, out


def test_with_ai_nothing_is_skipped(monkeypatch, capsys):
    server = _Iris(ai=True)
    code, out = _run(monkeypatch, capsys, server)
    assert code == 0, out
    assert sum(d.endswith(MCP) for d in server.loaded) == 3
    assert "skipped" not in out, out


def test_a_class_that_should_compile_and_does_not_is_still_an_error(monkeypatch, capsys):
    server = _Iris(ai=False, broken=("Graph/KG/PageRank.cls",))
    code, out = _run(monkeypatch, capsys, server)
    assert code == 1, out
    assert "PageRank.cls" in out and "1 compile error(s)" in out, out


def test_without_embedded_python_the_python_classes_are_skipped_too(monkeypatch, capsys):
    """Setup's rule, all of it: IVG_EMBEDDED_PYTHON=0 is a server without embedded Python."""
    server = _Iris(ai=True)
    monkeypatch.setenv("IVG_EMBEDDED_PYTHON", "0")
    stub_env = os.environ.get("IVG_EMBEDDED_PYTHON")
    code, out = _run(monkeypatch, capsys, server, keep_embedded_python=True)
    assert stub_env == "0" and code == 0, out
    assert "skipped Graph.KG.PyOps: needs embedded Python" in out, out
    assert "skipped Graph.KG.Service: depends on Graph.KG.PyOps" in out, out
    assert not any(d.endswith("Graph/KG/PyOps.cls") for d in server.loaded)
