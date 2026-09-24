"""Spec 230 US3 (T041, FR-010) — the graph key is the first subscript, everywhere.

`^KG("prop")`, `^KG("label")` and `^KG("deg2p")` were keyed by node, label and
predicate alone, so a per-graph erase could only reach them through the node IDs it
was deleting — and after spec 227 re-keyed `Graph_KG.nodes` to
`UNIQUE (graph_id, node_id)` the same ID lives in two graphs, so killing one
graph's entry killed the other's (`Eraser.cls:59-68` said so in a comment).

This is a text gate rather than a behavioural one because the failure it guards is
a site left behind. A reader still on the flat layout does not error: `$Data` and
`$Order` on a subscript that does not exist answer "nothing here", so a
label-filtered traversal quietly stops matching and a property map quietly comes
back empty. Nothing in a passing suite would say which of thirteen call sites was
missed, so the suite asserts on all of them.

The rule: a reference to one of these stores either names the whole subtree — a
`Kill` of everything, which is honest — or its first subscript is a graph key.
Nothing may sit between the store name and the graph.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

IRIS_SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src"

#: The stores this story re-keys. `deg2p_exact` travels with `deg2p`: it is the same
#: statistic computed exactly instead of by summing, written and read beside it, and
#: leaving it flat would leave `KHop2CountExact` answering from every graph.
REKEYED = ("prop", "label", "deg2p", "deg2p_exact")

#: Names a graph key is held in. Deliberately a closed set: a site that reaches the
#: store through some other variable has to say so here, which is the review this
#: gate exists to force.
GRAPH_KEY_TOKENS = frozenset(
    {
        "g",  # the loop variable in the rebuild's per-graph walk
        "gg",  # the inner graph in a merged walk
        "tKey",  # Graph.KG.Eraser, from GraphKey.ForIndex
        "pGraph",  # a method parameter, or a default-graph-only method's constant
        "graphKey",
        "tGraph",
        "..GKey",  # Graph.KG.FHIRGraph's context property, from GraphKey.ForIndex (spec 231)
    }
)

#: The only store allowed to hold a two-hop count with no graph: Arno computes it
#: over ^NKG, which interns node IDs with no graph dimension, so its numbers cannot
#: be attributed to a graph at all (FR-011 — no entry's graph may be inferred).
MERGED_STORE = "deg2p_exact_merged"

#: `^KG("deg2p_exact_merged")` may be written by the Arno path, declared in the
#: inventory, and emptied by `EraseAll` — which is the only erase that can touch it,
#: since "all" has no attribution problem. A fourth file naming it would be a scoped
#: reader answering from a merged count, which is the leak this story closes.
MERGED_STORE_FILES = {"NKGAccelTraversal.cls", "GraphStores.cls", "Eraser.cls"}


def _cls_files() -> list:
    return sorted(IRIS_SRC.rglob("*.cls"))


def _first_subscript(text: str, at: int) -> str | None:
    """The first subscript of the reference starting at `at`, or None for a whole
    subtree reference like `Kill ^KG("prop")`.

    `at` sits just past the store name, so the comma separating it from the graph key
    is the first thing here and is stepped over; without that the scan would end on
    it immediately and report an empty subscript for every correct site.
    """
    depth = 0
    token = ""
    if text[at : at + 1] == ",":
        at += 1
    for ch in text[at:]:
        if ch in "(":
            depth += 1
            token += ch
            continue
        if ch == ")":
            if depth == 0:
                return None  # closed with no subscript: the whole subtree
            depth -= 1
            token += ch
            continue
        if ch == "," and depth == 0:
            return token.strip()
        token += ch
    return token.strip()


def _references(store: str):
    """Every `^KG("<store>"` reference in the ObjectScript tree, with its first
    subscript. Yields `(path, line_number, line, first_subscript)`."""
    pattern = re.compile(r'\^KG\(\s*"' + re.escape(store) + r'"\s*')
    for path in _cls_files():
        text = path.read_text()
        for match in pattern.finditer(text):
            line_number = text.count("\n", 0, match.start()) + 1
            line = text.splitlines()[line_number - 1]
            if line.lstrip().startswith("//"):
                continue  # prose, not a reference
            yield path, line_number, line.strip(), _first_subscript(text, match.end())


@pytest.mark.parametrize("store", REKEYED)
def test_every_reference_leads_with_a_graph_key(store):
    offenders = []
    seen = 0
    for path, line_number, line, subscript in _references(store):
        if store == "deg2p" and MERGED_STORE in line:
            continue  # a `deg2p_exact_merged` reference, matched by the prefix
        seen += 1
        if subscript is None:
            continue  # the whole subtree
        if subscript in GRAPH_KEY_TOKENS:
            continue
        if "GraphKey).ForIndex(" in subscript:
            continue
        offenders.append(f"{path.name}:{line_number}: {line}")

    assert seen, f'no reference to ^KG("{store}") found — has it been renamed?'
    assert not offenders, (
        f'these ^KG("{store}") references do not lead with a graph key, so they '
        "address the pre-4.0.0 flat layout:\n  " + "\n  ".join(offenders)
    )


def test_no_reference_keeps_the_pre_214_zero_subscript():
    """`^KG("out", graph, 0, s, p, o)` was the layout spec 214 shipped and then
    dropped; the writer at `TraversalBuild.cls:70` has five subscripts after the
    graph. A reader still passing the `0` finds nothing and reports zero, which is
    how `Build2HopExactStats` came to answer 0 for a database with two-hop paths
    (FR-015).
    """
    pattern = re.compile(r'\^KG\(\s*"(?:out|in)"\s*,\s*[^,)]+,\s*0\s*,')
    offenders = []
    for path in _cls_files():
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if line.lstrip().startswith("//"):
                continue
            if pattern.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")

    assert not offenders, (
        "these references pass a literal 0 where the node belongs, so they read a "
        "subtree no writer fills:\n  " + "\n  ".join(offenders)
    )


def test_the_inventory_declares_all_four_graph_scoped():
    """`Graph.KG.GraphStores` is the one declaration every erase and verification
    reads (ADR-0004). A store that gains a graph key and is still listed as
    unscoped makes the verification report a hole that no longer exists — and,
    worse, tells the next reader the store cannot be split by graph.
    """
    inventory = (IRIS_SRC / "Graph" / "KG" / "GraphStores.cls").read_text()

    for store in REKEYED:
        entry = re.search(
            r'\.\.Entry\("\^KG\(""' + re.escape(store) + r'""\)",\s*"global",\s*'
            r'"([^"]*)",\s*(\d)',
            inventory,
        )
        assert entry, f'the inventory does not list ^KG("{store}")'
        locator, scoped = entry.group(1), entry.group(2)
        assert scoped == "1", f'^KG("{store}") is still declared unscoped'
        assert locator == "subscript 2", (
            f'^KG("{store}") is declared graph-scoped at "{locator}", but the graph '
            "key is the first subscript after the store name"
        )


def test_the_merged_two_hop_store_is_declared_unscoped():
    """The honest home for a count Arno cannot attribute to a graph. Declared rather
    than dropped, so the merged sketch stays available to a caller that wants it
    while no scoped reader can be fed by it."""
    inventory = (IRIS_SRC / "Graph" / "KG" / "GraphStores.cls").read_text()

    entry = re.search(
        r'\.\.Entry\("\^KG\(""' + MERGED_STORE + r'""\)",\s*"global",\s*"[^"]*",\s*(\d)',
        inventory,
    )
    assert entry, f'the inventory does not list ^KG("{MERGED_STORE}")'
    assert entry.group(1) == "0", (
        f'^KG("{MERGED_STORE}") is declared graph-scoped, but it is built from ^NKG, '
        "whose interning has no graph dimension"
    )


def test_only_the_arno_path_and_the_inventory_name_the_merged_store():
    """Code, not prose: a docstring may say where the merged count lives — that is how
    the next reader finds it — but only these files may address the global."""
    naming = {
        path.name
        for path in _cls_files()
        for line in path.read_text().splitlines()
        if (MERGED_STORE in line) and not line.lstrip().startswith(("//", "///"))
    }

    assert naming, f"nothing writes ^KG(\"{MERGED_STORE}\")"
    assert naming <= MERGED_STORE_FILES, (
        f'these files reach ^KG("{MERGED_STORE}") outside the Arno path: '
        f"{sorted(naming - MERGED_STORE_FILES)}. A scoped reader answering from a "
        "merged count is the leak FR-010 closes."
    )
