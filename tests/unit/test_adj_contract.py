"""Contract for `Graph.KG.Adj` (ADR-0010): Degree, NextSource, HasEdges.

Two parts, both container-free:

1. A Python reference model of the contract. The model derives the
   `^KG("out", g, s, p, o[, "#"])` tree from a set of live edge rows the way
   `Graph.KG.EdgeScan` lays it out (a triple with one edge is a bare leaf; a
   triple with two or more carries `"#"` = live edge count), then runs the same
   read algorithm the ObjectScript runs. Property-style cases check the model
   against the row count, which is what "exact" means in ADR-0010.

2. A source scan of Adj.cls: it declares exactly the three ClassMethods, with
   exactly the contract signatures, and nothing else.

The ObjectScript itself is checked live in tests/e2e/test_adj_degree_live.py.
"""

from pathlib import Path
import random
import re

import pytest

ADJ_CLS = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "Adj.cls"

CONTRACT = {
    "Degree": "ClassMethod Degree(pGraph As %String, pSrc As %String, pPred As %String = \"\") As %Integer",
    "NextSource": "ClassMethod NextSource(pGraph As %String, pSrc As %String = \"\") As %String",
    "HasEdges": "ClassMethod HasEdges(pGraph As %String, pSrc As %String, pPred As %String = \"\") As %Boolean",
}

#: Spec 248 added the degree cache to this class. The three signatures above are unchanged; these
#: are the methods the cache needs: the recount (step 3 of Degree), the per-source warm fill and
#: its two drivers. Changed on purpose: the test used to pin "exactly the three contract methods".
CACHE_METHODS = {
    "Count": "ClassMethod Count(pGraph As %String, pSrc As %String, pPred As %String = \"\") As %Integer",
    "WarmFill": "ClassMethod WarmFill(pGraph As %String, pSrc As %String) As %Integer",
    "WarmAll": "ClassMethod WarmAll(pGraph As %String) As %Integer",
    "WarmCache": "ClassMethod WarmCache() As %Integer",
    # The whole-graph stamp readers check once so a bulk pass costs one global read per node (ADR-0012).
    "AllWarm": "ClassMethod AllWarm(pGraph As %String) As %Boolean",
}
ALL_METHODS = {**CONTRACT, **CACHE_METHODS}


# --- reference model -----------------------------------------------------------------


def layout(rows):
    """The `^KG("out")` tree for a set of live edges.

    `rows` is a set of (graph, s, p, o, ekey). Returns
    `{g: {s: {p: {o: count}}}}` where `count` is the number of live ekeys, which
    is what the leaf (plus its "#" counter when count >= 2) stands for.
    A bare leaf (count 1) is the same as a "#"-less leaf in the globals.
    """
    tree = {}
    counts = {}
    for g, s, p, o, _ekey in rows:
        counts[(g, s, p, o)] = counts.get((g, s, p, o), 0) + 1
    for (g, s, p, o), n in counts.items():
        tree.setdefault(g, {}).setdefault(s, {}).setdefault(p, {})[o] = n
    return tree


def _leaf_contribution(n):
    # Degree adds the "#" counter when it is set (multigraph, 2+ edges), else 1.
    return n if n >= 2 else 1


def degree(tree, g, s, p=""):
    """Mirror of Graph.KG.Adj.Degree: sum over each o of the leaf contribution."""
    if s == "":
        return 0
    total = 0
    preds = tree.get(g, {}).get(s, {})
    for pred in sorted(preds) if p == "" else [p] if p in preds else []:
        for o in sorted(preds[pred]):
            total += _leaf_contribution(preds[pred][o])
    return total


def next_source(tree, g, s=""):
    """Mirror of `Quit $Order(^KG("out", g, s))`: the next source after s, "" at the end."""
    later = [src for src in tree.get(g, {}) if src > s]
    return min(later) if later else ""


def has_edges(tree, g, s, p=""):
    """Mirror of `$Data(^KG("out",g,s))` / `$Data(^KG("out",g,s,p))` as a boolean."""
    if s == "":
        return False
    preds = tree.get(g, {}).get(s, {})
    if p == "":
        return bool(preds)
    return p in preds and bool(preds[p])


def sources(tree, g):
    return sorted(tree.get(g, {}))


def _random_rows(rng, n_graphs, n_nodes, n_preds, n_objs, n_edges):
    graphs = [""] + [f"g{i}" for i in range(n_graphs)]
    rows = set()
    for _ in range(n_edges):
        g = rng.choice(graphs)
        s = f"n{rng.randrange(n_nodes)}"
        p = f"P{rng.randrange(n_preds)}"
        o = f"n{rng.randrange(n_objs)}"
        ekey = rng.randrange(4)
        rows.add((g, s, p, o, ekey))
    return rows


# --- contract, on a hand-built graph -------------------------------------------------


def test_single_edge_is_a_bare_leaf_and_counts_one():
    tree = layout({("", "a", "KNOWS", "b", 0)})
    assert degree(tree, "", "a") == 1
    assert degree(tree, "", "a", "KNOWS") == 1
    assert degree(tree, "", "a", "LIKES") == 0


def test_parallel_edges_each_count():
    # Three live edges on one triple: "#" = 3, and Degree adds 3, not 1.
    rows = {("", "a", "KNOWS", "b", k) for k in (0, 1, 2)}
    tree = layout(rows)
    assert tree[""]["a"]["KNOWS"]["b"] == 3
    assert degree(tree, "", "a") == 3
    assert degree(tree, "", "a", "KNOWS") == 3


def test_drop_back_to_one_edge_counts_one():
    rows = {("", "a", "KNOWS", "b", 0), ("", "a", "KNOWS", "b", 1)}
    assert degree(layout(rows), "", "a") == 2
    rows.discard(("", "a", "KNOWS", "b", 1))
    assert degree(layout(rows), "", "a") == 1


def test_degree_counts_distinct_targets_and_predicates():
    rows = {
        ("", "a", "KNOWS", "b", 0),
        ("", "a", "KNOWS", "c", 0),
        ("", "a", "LIKES", "b", 0),
    }
    tree = layout(rows)
    assert degree(tree, "", "a") == 3
    assert degree(tree, "", "a", "KNOWS") == 2
    assert degree(tree, "", "a", "LIKES") == 1


def test_missing_source_and_empty_source_are_zero():
    tree = layout({("", "a", "KNOWS", "b", 0)})
    assert degree(tree, "", "zz") == 0
    assert degree(tree, "", "") == 0
    assert has_edges(tree, "", "zz") is False
    assert has_edges(tree, "", "") is False


def test_graph_scope_is_respected():
    tree = layout({("g1", "a", "KNOWS", "b", 0), ("", "a", "KNOWS", "c", 0)})
    assert degree(tree, "g1", "a") == 1
    assert degree(tree, "", "a") == 1
    assert degree(tree, "g2", "a") == 0


def test_next_source_enumerates_sources_then_ends():
    tree = layout({("", "b", "P", "x", 0), ("", "a", "P", "x", 0), ("", "c", "P", "a", 0)})
    seen = []
    s = next_source(tree, "")
    while s != "":
        seen.append(s)
        s = next_source(tree, "", s)
    assert seen == ["a", "b", "c"]


def test_next_source_never_lists_a_node_with_no_outgoing_edge():
    # A sink ("x" only appears as a target) is not a source.
    tree = layout({("", "a", "P", "x", 0)})
    assert next_source(tree, "", "") == "a"
    assert next_source(tree, "", "a") == ""


def test_has_edges_predicate_form():
    tree = layout({("", "a", "KNOWS", "b", 0)})
    assert has_edges(tree, "", "a") is True
    assert has_edges(tree, "", "a", "KNOWS") is True
    assert has_edges(tree, "", "a", "LIKES") is False
    assert has_edges(tree, "", "b") is False


# --- property-style: the model equals the row count ----------------------------------


@pytest.mark.parametrize("seed", range(40))
def test_degree_equals_row_count_over_random_graphs(seed):
    rng = random.Random(seed)
    rows = _random_rows(rng, n_graphs=2, n_nodes=6, n_preds=3, n_objs=6, n_edges=60)
    tree = layout(rows)
    for g in ["", "g0", "g1"]:
        for s in [f"n{i}" for i in range(6)]:
            expect = sum(1 for r in rows if r[0] == g and r[1] == s)
            assert degree(tree, g, s) == expect, (seed, g, s)
            for p in [f"P{i}" for i in range(3)]:
                expect_p = sum(1 for r in rows if r[0] == g and r[1] == s and r[2] == p)
                assert degree(tree, g, s, p) == expect_p, (seed, g, s, p)


@pytest.mark.parametrize("seed", range(40))
def test_next_source_lists_exactly_the_sources_with_rows(seed):
    rng = random.Random(1000 + seed)
    rows = _random_rows(rng, n_graphs=1, n_nodes=8, n_preds=2, n_objs=8, n_edges=30)
    tree = layout(rows)
    for g in ["", "g0"]:
        expect = sorted({r[1] for r in rows if r[0] == g})
        got = []
        s = next_source(tree, g, "")
        while s != "":
            got.append(s)
            s = next_source(tree, g, s)
        assert got == expect, (seed, g)


@pytest.mark.parametrize("seed", range(40))
def test_has_edges_agrees_with_degree(seed):
    rng = random.Random(2000 + seed)
    rows = _random_rows(rng, n_graphs=1, n_nodes=5, n_preds=3, n_objs=5, n_edges=20)
    tree = layout(rows)
    for s in [f"n{i}" for i in range(5)]:
        assert has_edges(tree, "", s) == (degree(tree, "", s) > 0)
        for p in [f"P{i}" for i in range(3)]:
            assert has_edges(tree, "", s, p) == (degree(tree, "", s, p) > 0)


@pytest.mark.parametrize("seed", range(20))
def test_rolled_back_insert_changes_nothing(seed):
    # A rolled-back write never reaches the derived tree, because the tree is a
    # function of the committed rows. With a stored counter it would not be.
    rng = random.Random(3000 + seed)
    rows = _random_rows(rng, n_graphs=1, n_nodes=5, n_preds=2, n_objs=5, n_edges=25)
    before = layout(rows)
    _uncommitted = rows | {("", "n0", "P0", "n4", 99)}  # the insert that rolls back
    assert layout(rows) == before
    assert degree(layout(rows), "", "n0") == degree(before, "", "n0")


@pytest.mark.parametrize("seed", range(20))
def test_deleting_every_edge_leaves_no_source(seed):
    rng = random.Random(4000 + seed)
    rows = _random_rows(rng, n_graphs=1, n_nodes=5, n_preds=2, n_objs=5, n_edges=25)
    for r in list(rows):
        rows.discard(r)
    tree = layout(rows)
    assert sources(tree, "") == []
    assert next_source(tree, "", "") == ""
    assert degree(tree, "", "n0") == 0


# --- source scan of Adj.cls ----------------------------------------------------------

_CLASSMETHOD = re.compile(r"^\s*(ClassMethod|Method|Property|Parameter|Query|Index|Trigger)\b(.*)$", re.M)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def test_adj_cls_declares_the_three_contract_methods_and_the_cache_methods():
    source = ADJ_CLS.read_text(encoding="utf-8")
    assert re.search(r"^Class Graph\.KG\.Adj\b", source, re.M), "class name"
    code = "\n".join(line.split("//", 1)[0] for line in source.splitlines())
    members = [_norm(m.group(0)) for m in re.finditer(r"^\s*ClassMethod\s+\w+\(.*?\)\s*As\s+%\w+", code, re.M)]
    assert sorted(members) == sorted(ALL_METHODS.values()), members


def test_adj_cls_declares_no_other_members():
    source = ADJ_CLS.read_text(encoding="utf-8")
    code = "\n".join(line.split("//", 1)[0] for line in source.splitlines())
    declared = [_norm(m.group(0)) for m in _CLASSMETHOD.finditer(code)]
    assert len(declared) == len(ALL_METHODS), declared
    for line in declared:
        assert line.startswith("ClassMethod "), line
