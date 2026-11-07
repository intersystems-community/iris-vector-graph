"""4.6.0: Graph.KG.MCPToolSet compiles after Graph.KG.MCPTools, so a class deploy cannot fail at random on it.

`%AI.ToolSet`'s `%Discover` and `%Invoke` generators read the classes a ToolSet's XData `<Include Class=...>` names.
Nothing told the compiler so, and a multi-process compile of the package ran MCPToolSet's generators before MCPTools
had its inheritance resolved: ERROR #5289, "Detected 2 errors during load", on a fresh container of the dpgenai1 gate
(2026-11-07), and on a rerun nothing. The deploys excused it instead: scripts/tck/deploy_namespace.py printed "only
the known Graph.KG.MCPToolSet error" and left the class out of its stale-class check, and tests/conftest.py dropped
any deploy error naming MCPTool, both saying MCPTools "is not in iris_src", which it is. MCPToolSet now declares
`DependsOn` on every class it includes, and the excuses are gone: a compile error there is an error.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "iris_src" / "src"


def _classes() -> dict:
    out = {}
    for p in SRC.rglob("*.cls"):
        text = p.read_text()
        m = re.search(r"^Class\s+([\w.%]+)", text, re.M)
        if m:
            out[m.group(1)] = text
    return out


def _depends_on(text: str) -> set:
    """The classes the `DependsOn` keyword of the class definition line names (one name, or a list in parentheses)."""
    head = re.search(r"^Class\s+[\w.%]+[^\n]*", text, re.M).group(0)
    m = re.search(r"DependsOn\s*=\s*(\([^)]*\)|[\w.%]+)", head)
    return {n.strip() for n in m.group(1).strip("()").split(",")} if m else set()


def _toolset_includes(text: str) -> set:
    return set(re.findall(r'<Include\s+Class="([\w.%]+)"', text))


def test_every_toolset_depends_on_the_classes_it_includes():
    toolsets = {n: t for n, t in _classes().items() if _toolset_includes(t)}
    assert "Graph.KG.MCPToolSet" in toolsets  # the scan sees the class this is about
    missing = {n: sorted(_toolset_includes(t) - _depends_on(t)) for n, t in toolsets.items()
               if _toolset_includes(t) - _depends_on(t)}
    assert missing == {}, f"add DependsOn for the included classes: {missing}"


def test_mcp_toolset_depends_on_mcp_tools():
    assert "Graph.KG.MCPTools" in _depends_on(_classes()["Graph.KG.MCPToolSet"])


def test_the_dependency_parser_reads_one_name_and_a_list():
    assert _depends_on("Class A.B Extends C [ DependsOn = X.Y ]\n{") == {"X.Y"}
    assert _depends_on("Class A.B Extends C [ Abstract, DependsOn = (X.Y, Z.W) ]\n{") == {"X.Y", "Z.W"}
    assert _depends_on("Class A.B Extends C\n{\n/// DependsOn = X.Y\n") == set()


def _deploy_namespace():
    spec = importlib.util.spec_from_file_location("deploy_namespace", ROOT / "scripts" / "tck" / "deploy_namespace.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _StaleIris:
    def __init__(self, stale):
        self.stale = set(stale)

    def classMethodValue(self, cls, method, *args):
        if (cls, method) == ("%SYSTEM.OBJ", "IsUpToDate"):
            return 0 if args[0] in self.stale else 1
        if (cls, method) == ("%SYSTEM.OBJ", "CompileList"):
            return "st"
        raise AssertionError((cls, method))


def test_tck_deploy_reports_an_uncompiled_mcp_toolset():
    m = _deploy_namespace()
    assert not hasattr(m, "KNOWN_UNCOMPILED")
    assert m.recompile_stale(_StaleIris({"Graph.KG.MCPToolSet"}), ["Graph.KG.MCPToolSet"]) == ["Graph.KG.MCPToolSet"]


def test_no_deploy_excuses_an_mcp_compile_error():
    text = (ROOT / "scripts" / "tck" / "deploy_namespace.py").read_text()
    assert "only the known" not in text and '"MCPTool" in line' not in text
    assert '"MCPTool" not in e' not in (ROOT / "tests" / "conftest.py").read_text()
