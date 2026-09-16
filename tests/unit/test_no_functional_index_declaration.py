"""No class may declare a functional index against Graph.KG.GraphIndex.

`Graph.KG.Edge` declared `Index GraphIdx On (s, p, oId, qualifiers) As
Graph.KG.GraphIndex`, and `Graph.KG.TestEdge` declared the same index. Both were
live: when the index is compiled, every SQL insert into `Graph_KG.rdf_edges`
fires `GraphIndex.InsertIndex`, which writes

    ^KG("out", 0, s, p, o)      <- graph key hardcoded to 0
    ^KG("deg", s)               <- no graph key at all
    ^KG("degp", s, p)           <- no graph key at all

So an edge inserted with `graph_id = 'acme'` lands in the *default* graph's
adjacency and never appears under its own graph key, and the degree counters sit
at the wrong subscript depth. Both violate ADR-0001 (graph key is always the
first subscript). `PurgeIndex` additionally did a bare `Kill ^KG`, which
destroys the temporal subtrees that `TraversalBuild` is careful to preserve.

The declaration also made `Graph.KG.Edge` uncompilable via `%SYSTEM.OBJ.LoadDir`,
which compiles a directory in name order: `Edge` is always reached before
`GraphIndex`, so the forward dependency never resolves and the load fails with
ERROR #5289, cascading into a missing `Graph_KG.rdf_edges` and a failed
`TraversalBuild.BuildKG` compile.

`Graph.KG.GraphIndex` itself survives: `EdgeScan`, `TraversalBuild` and
`NKGAccelTraversal` call its `^NKG` interning and HLL helpers. Only its role as
a functional index is removed.
"""

from pathlib import Path
import re

import pytest

IRIS_SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src"
GRAPH_INDEX = IRIS_SRC / "Graph" / "KG" / "GraphIndex.cls"

_ALL_CLASSES = sorted(IRIS_SRC.rglob("*.cls"))

# `Index <name> On (...) As <SomeClass>` — a functional index declaration.
_FUNCTIONAL_INDEX = re.compile(r"^\s*Index\s+\w+\s+On\s+.*\bAs\s+Graph\.KG\.GraphIndex", re.M)

# Hooks that only exist to serve %Library.FunctionalIndex.
_INDEX_HOOKS = ("InsertIndex", "UpdateIndex", "PurgeIndex", "SortBeginIndex", "SortEndIndex")


@pytest.mark.parametrize("cls_path", _ALL_CLASSES, ids=lambda p: p.name)
def test_no_class_declares_a_graphindex_functional_index(cls_path):
    """No persistent class may attach GraphIndex as a functional index."""
    source = cls_path.read_text(encoding="utf-8", errors="replace")
    match = _FUNCTIONAL_INDEX.search(source)
    assert match is None, (
        f"{cls_path.name} declares a functional index against Graph.KG.GraphIndex "
        f"({match.group(0).strip() if match else ''}). That index writes "
        f'^KG("out", 0, ...) for every graph and ^KG("deg", s) with no graph key.'
    )


def test_graphindex_is_not_a_functional_index():
    """GraphIndex no longer extends %Library.FunctionalIndex.

    While it does, IRIS treats it as an index implementation and any surviving
    declaration silently re-enables the cross-graph writes.
    """
    source = GRAPH_INDEX.read_text(encoding="utf-8", errors="replace")
    declaration = re.search(r"^\s*Class\s+Graph\.KG\.GraphIndex\s+Extends\s+([^\s{\[]+)", source, re.M)
    assert declaration is not None, "Cannot find the Class declaration in GraphIndex.cls"
    assert "FunctionalIndex" not in declaration.group(1), (
        f"Graph.KG.GraphIndex extends {declaration.group(1)}. While it extends "
        "%Library.FunctionalIndex, IRIS treats it as an index implementation and "
        "any surviving declaration silently re-enables the cross-graph writes."
    )


@pytest.mark.parametrize("hook", _INDEX_HOOKS)
def test_graphindex_has_no_index_hooks(hook):
    """The functional-index entry points are removed."""
    source = GRAPH_INDEX.read_text(encoding="utf-8", errors="replace")
    assert f"ClassMethod {hook}(" not in source, (
        f"Graph.KG.GraphIndex still defines {hook}(), a %Library.FunctionalIndex hook."
    )


def test_graphindex_does_not_kill_kg_wholesale():
    """No bare `Kill ^KG` survives — it would destroy the temporal subtrees."""
    source = GRAPH_INDEX.read_text(encoding="utf-8", errors="replace")
    assert not re.search(r"^\s*Kill\s+\^KG\s*$", source, re.M), (
        "Graph.KG.GraphIndex contains a bare `Kill ^KG`, which destroys the "
        'temporal subtrees ("tout", "tin", "bucket", "tagg", "edgeprop").'
    )


def test_interning_helpers_survive():
    """The helpers three other classes depend on are still present.

    Guards against 'delete GraphIndex.cls' being taken literally: EdgeScan,
    TraversalBuild and NKGAccelTraversal all call into these.
    """
    source = GRAPH_INDEX.read_text(encoding="utf-8", errors="replace")
    for helper in (
        "InitStructuralLabels",
        "InternNode",
        "InternLabel",
        "UpdateStructuralHLL",
        "EmptyHLL",
        "MergeHLL",
        "EstimateHLL",
        "GetNodeIdx",
        "GetLabelIdx",
    ):
        assert f"ClassMethod {helper}(" in source, (
            f"Graph.KG.GraphIndex.{helper}() is gone but callers remain."
        )
