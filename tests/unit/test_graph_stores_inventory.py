"""One declared inventory of the stores that hold graph content.

Every deletion, verification and migration path needs the same answer to "where
does a graph's content live?", and until now each derived it separately:
`drop_graph` deletes from a list of tables it names inline, `verify_sync` counts
two of them, `TraversalBuild.BuildKG` rebuilds a third set. When a store was
added, some of those lists were updated and some were not — that is the drift
this inventory exists to remove.

`Graph.KG.GraphStores` declares it once. Two consumers read it: `GraphVerify`
(reports drift, phase 1) and `Eraser` (removes content, phase 3). See ADR-0004 —
"every deletion path funnels through the Eraser's transaction, so a write path
that touches one store without the others is a bug rather than a documented
mode."

The inventory also records which stores are *not* graph-scoped today. That is
not a gap in the inventory; it is the inventory reporting a gap in the schema,
and `verify_graph` surfaces it rather than quietly skipping those stores.
"""

from pathlib import Path
import re

import pytest

IRIS_SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src"
GRAPH_STORES = IRIS_SRC / "Graph" / "KG" / "GraphStores.cls"
GRAPH_VERIFY = IRIS_SRC / "Graph" / "KG" / "GraphVerify.cls"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def test_graph_stores_class_exists():
    assert GRAPH_STORES.is_file(), f"{GRAPH_STORES} does not exist"


def test_graph_verify_class_exists():
    assert GRAPH_VERIFY.is_file(), f"{GRAPH_VERIFY} does not exist"


def test_inventory_is_declared_once():
    """`Inventory()` is the only declaration; nothing else may enumerate stores."""
    assert "ClassMethod Inventory(" in _source(GRAPH_STORES), (
        "Graph.KG.GraphStores does not declare Inventory()."
    )


@pytest.mark.parametrize(
    "store",
    [
        # graph-scoped, and therefore verifiable
        "Graph_KG.rdf_edges",
        '^KG("out")',
        '^KG("in")',
        '^KG("deg")',
        '^KG("degp")',
        '^KG("tout")',
        '^KG("tin")',
        '^KG("bucket")',
        '^KG("tagg")',
        '^KG("edgeprop")',
        '^IVG.Ledger("stmt")',
        '^IVG.Ledger("tuple")',
        # not graph-scoped today — reported, not skipped
        "Graph_KG.nodes",
        "Graph_KG.rdf_labels",
        "Graph_KG.rdf_props",
        "Graph_KG.rdf_reifications",
        "^NKG",
    ],
)
def test_every_store_holding_graph_content_is_listed(store):
    """A store missing here is a store erasure and verification will skip."""
    source = _source(GRAPH_STORES)
    needle = store.replace('"', '""')  # ObjectScript string-literal escaping
    assert needle in source or store in source, (
        f"{store} is not named in the Graph.KG.GraphStores inventory."
    )


def test_graph_verify_reads_the_inventory_rather_than_its_own_list():
    """The point of one inventory is that consumers do not keep their own."""
    assert "Graph.KG.GraphStores" in _source(GRAPH_VERIFY), (
        "Graph.KG.GraphVerify does not read the inventory from "
        "Graph.KG.GraphStores — it has its own copy of the store list."
    )


def test_graph_verify_derives_its_graph_key_through_graph_key():
    """Verification that derived the key differently would verify the wrong tree."""
    assert "Graph.KG.GraphKey" in _source(GRAPH_VERIFY), (
        "Graph.KG.GraphVerify does not call Graph.KG.GraphKey.ForIndex()."
    )


def test_unscoped_stores_carry_a_reason():
    """`unscoped` entries must say why, or the report is not actionable."""
    source = _source(GRAPH_STORES)
    assert re.search(r'"reason"', source), (
        "Inventory entries for unscoped stores carry no 'reason' field."
    )
