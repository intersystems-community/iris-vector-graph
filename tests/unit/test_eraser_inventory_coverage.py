"""The Eraser reaches every store `Graph.KG.GraphStores` declares.

`Graph.KG.GraphStores` exists because four modules each derived their own answer
to "where does a graph's content live?" and drifted apart: `drop_graph` named five
tables inline, `verify_sync` counted two of them, `BuildKG` rebuilt a different
set, `restore_snapshot` cleared a fifth list, and none of them touched the
temporal globals or the ledger.

Declaring the inventory does not by itself stop that. What stops it is a check
that fails when a store is added to the inventory and not to the consumer. These
tests read both `.cls` files as source, so they run without a container — the
behaviour is in `tests/integration/test_erase_graph.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG"
STORES = SRC / "GraphStores.cls"
ERASER = SRC / "Eraser.cls"


def _inventory_entries():
    """(name, graphScoped) for every `..Entry(...)` in the inventory."""
    text = STORES.read_text()
    entries = []
    for match in re.finditer(
        r'\.\.Entry\(\s*"((?:[^"]|"")*)"\s*,\s*"(\w+)"\s*,\s*"[^"]*"\s*,\s*(\d)', text
    ):
        entries.append((match.group(1).replace('""', '"'), int(match.group(3))))
    return entries


def test_the_inventory_parses():
    """Guard the guard: a regex that matches nothing would pass every test below."""
    entries = _inventory_entries()
    assert len(entries) >= 17, f"parsed only {len(entries)} inventory entries"
    names = [name for name, _ in entries]
    assert "Graph_KG.rdf_edges" in names
    assert '^KG("out")' in names
    assert '^IVG.Ledger("tuple")' in names


@pytest.mark.parametrize("store", [name for name, scoped in _inventory_entries() if scoped])
def test_every_graph_scoped_store_is_named_in_the_eraser(store):
    """A graph-scoped store the Eraser does not name is content that outlives erasure."""
    text = ERASER.read_text()
    # ^KG("out") in the inventory is Kill ^KG("out", key, ...) in the Eraser, so
    # match on the subscript name rather than the inventory's display spelling.
    needle = store
    match = re.match(r'\^(\w[\w.]*)\("(\w+)"\)', store)
    if match:
        needle = f'^{match.group(1)}("{match.group(2)}"'
    assert needle in text, f"{store} is in the inventory but the Eraser never touches it"


@pytest.mark.parametrize("store", [name for name, scoped in _inventory_entries() if not scoped])
def test_every_unscoped_store_is_named_in_the_eraser(store):
    """`erase_all` has no attribution problem, so it must empty these too.

    They are the stores no per-graph erase can reach — keyed by node id alone, or
    append-only and graph-blind. That is exactly why they accumulate.
    """
    text = ERASER.read_text()
    needle = store
    match = re.match(r"\^(\w[\w.]*)$", store)
    if match:
        needle = f"^{match.group(1)}"
    assert needle in text, f"{store} is in the inventory but erase_all never empties it"


def test_the_eraser_preserves_the_operation_records():
    """ADR-0004: erasure removes content, not history.

    `^IVG.Ledger("rec", seq, ordinal)` carries the graph per *record*, so one
    revision's ordinals can belong to several graphs. Pruning it by graph punches
    holes in the ordinal sequence of revisions describing graphs the caller did
    not ask to erase.
    """
    text = ERASER.read_text()
    assert 'Kill ^IVG.Ledger("rec"' not in text
    assert '^IVG.Ledger("rec"' in text, (
        "the Eraser should say in its own source why it leaves the records alone"
    )
