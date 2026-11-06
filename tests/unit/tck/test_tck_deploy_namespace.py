"""scripts/tck/deploy_namespace.py leaves no class uncompiled and says so when it cannot (4.6.0 R310).

Into a fresh namespace the classes compiled before the schema existed, so Graph.KG.TraversalBuild (embedded SQL
over the tables) failed to compile. The compile check only looked at lines holding "ERROR", printed "only the known
Graph.KG.MCPToolSet error" and exited 0; every later `engine.sync()` failed `<CLASS DOES NOT EXIST>
BuildKG+1^Graph.KG.Traversal.1 *Graph.KG.TraversalBuild`, which the TCK harness then dropped (940 scenarios on
2026.3). The deploy now recompiles, after the schema, every class that is not up to date, and fails naming any that
still is not, apart from the known MCPToolSet.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _mod():
    spec = importlib.util.spec_from_file_location("deploy_namespace", ROOT / "scripts" / "tck" / "deploy_namespace.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class FakeIris:
    def __init__(self, stale, fixed_by_recompile=()):
        self.stale = set(stale)
        self.fixed = set(fixed_by_recompile)
        self.compiled = []

    def classMethodValue(self, cls, method, *args):
        if (cls, method) == ("%SYSTEM.OBJ", "IsUpToDate"):
            return 0 if args[0] in self.stale else 1
        if (cls, method) == ("%SYSTEM.OBJ", "CompileList"):
            names = [n[:-4] for n in args[0].split(",")]
            self.compiled.append(names)
            self.stale -= self.fixed & set(names)
            return "st"
        if (cls, method) == ("%SYSTEM.Status", "IsOK"):
            return 1
        raise AssertionError((cls, method))


def test_a_class_stale_after_the_first_compile_is_recompiled_after_the_schema():
    m = _mod()
    o = FakeIris({"Graph.KG.TraversalBuild"}, fixed_by_recompile={"Graph.KG.TraversalBuild"})
    left = m.recompile_stale(o, ["Graph.KG.Traversal", "Graph.KG.TraversalBuild"])
    assert o.compiled == [["Graph.KG.TraversalBuild"]]
    assert left == []


def test_a_class_that_stays_stale_is_reported():
    m = _mod()
    o = FakeIris({"Graph.KG.TraversalBuild"})
    assert m.recompile_stale(o, ["Graph.KG.TraversalBuild"]) == ["Graph.KG.TraversalBuild"]


def test_the_known_mcp_toolset_is_not_reported():
    m = _mod()
    o = FakeIris({"Graph.KG.MCPToolSet"})
    assert m.recompile_stale(o, ["Graph.KG.MCPToolSet"]) == []
    assert o.compiled == []


def test_nothing_stale_compiles_nothing():
    m = _mod()
    o = FakeIris(set())
    assert m.recompile_stale(o, ["Graph.KG.Traversal"]) == []
    assert o.compiled == []


def test_a_class_setup_skips_is_not_reported():
    """On Community the MCP classes name %AI classes the server lacks: initialize_schema skips them (R272), so does
    the deploy."""
    m = _mod()
    o = FakeIris({"Graph.KG.MCPService"})
    assert m.recompile_stale(o, ["Graph.KG.MCPService"], skip={"Graph.KG.MCPService"}) == []
    assert o.compiled == []
