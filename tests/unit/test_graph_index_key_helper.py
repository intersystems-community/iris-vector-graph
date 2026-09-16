"""One Python-side derivation of the ^KG index key, mirroring GraphKey.ForIndex.

ADR-0003 gives `Graph.KG.GraphKey` sole ownership of the two graph-key
derivations, and forbids callers re-deriving. Python cannot honour that
literally: `WriteAdjacency` and `DeleteAdjacency` take an *already-derived* key,
so the value has to exist before the DBAPI round trip, and asking IRIS for it
would add a round trip per edge in `create_edge`'s hot path.

So Python gets exactly one mirror — `graph_index_key` — and every call site uses
it. The rule these tests enforce is not "never derive" but "derive in one place":
the inline `graph if graph else 0` was written out at each call site, which is how
`delete_edge` ended up deriving a key it had never assigned.

The ObjectScript-side tests live in `test_adjacency_counter_ownership.py`; the
behaviour is locked in `tests/integration/test_default_graph_spelling.py`.
"""

from pathlib import Path
import re

import pytest

from iris_vector_graph._validate import graph_index_key

REPO = Path(__file__).resolve().parents[2]
NODES_EDGES = REPO / "iris_vector_graph" / "_engine" / "nodes_edges.py"
EDGE_SCAN = REPO / "iris_src" / "src" / "Graph" / "KG" / "EdgeScan.cls"


# --- the derivation itself ---------------------------------------------------


@pytest.mark.parametrize("empty", ["", None])
def test_the_default_graph_derives_to_the_integer_zero(empty):
    """Integer, not the string "0" — ADR-0001 keys the default graph with 0."""
    assert graph_index_key(empty) == 0
    assert isinstance(graph_index_key(empty), int)


def test_a_named_graph_derives_to_its_own_name():
    assert graph_index_key("acme") == "acme"


def test_the_reserved_name_zero_is_rejected():
    """IRIS canonicalizes the subscript "0" to the integer that keys default."""
    with pytest.raises(ValueError, match="reserved"):
        graph_index_key("0")


def test_a_control_character_is_rejected_before_the_round_trip():
    with pytest.raises(ValueError, match="control character"):
        graph_index_key("acme\x01")


def test_a_transport_nul_is_stripped_rather_than_rejected():
    """IRIS SQL returns $Char(0) for an empty-string VARCHAR; that is transport."""
    assert graph_index_key("acme\x00") == "acme"
    assert graph_index_key("\x00") == 0


# --- one derivation, not one per call site -----------------------------------


def test_no_call_site_derives_the_key_inline():
    """`graph if graph else 0`, written out per call site, is the defect.

    `delete_edge` had it in the `^KG` call but assigned `graph_id` only in its
    `else` branch, so `all_graphs=True` raised `UnboundLocalError` inside a `try`
    that logs and swallows — every row left SQL and every `^KG` entry stayed.
    """
    source = NODES_EDGES.read_text(encoding="utf-8")
    inline = re.findall(r"graph(?:_id)?\s+if\s+graph(?:_id)?\s+else\s+0", source)
    assert inline == [], (
        f"nodes_edges.py derives the index key inline {len(inline)} time(s): "
        f"{inline}. Use graph_index_key() so the rule has one home."
    )


def test_delete_edge_derives_the_key_before_it_branches():
    """The key must not depend on which DELETE branch ran."""
    source = NODES_EDGES.read_text(encoding="utf-8")
    start = source.index("def delete_edge(")
    body = source[start : source.index("\n    def ", start + 1)]
    key_at = body.index("graph_index_key(")
    branch_at = body.index("if all_graphs:")
    assert key_at < branch_at, (
        "delete_edge derives the index key after branching on all_graphs, so one "
        "branch can leave it unbound."
    )


# --- readers must see both spellings of the default graph --------------------


def test_the_deleting_readers_coalesce_the_nullable_graph_column():
    """`graph_id` is nullable and two writers disagree; readers must see both.

    `create_edge` writes `''`; every INSERT that omits the column writes NULL.
    `WHERE graph_id = ?` finds one half of the default graph and silently misses
    the other, so deletion reports success over rows it never touched.
    """
    source = NODES_EDGES.read_text(encoding="utf-8")
    bad = re.findall(r"(?:DELETE|SELECT)[^\n]*graph_id = \?", source)
    assert bad == [], (
        "these statements match only one spelling of the default graph: "
        f"{bad}. Use COALESCE(graph_id, '') = COALESCE(?, '')."
    )


def test_the_bulk_ingest_insert_names_the_graph_column():
    """A writer that omits graph_id creates the second spelling."""
    source = EDGE_SCAN.read_text(encoding="utf-8")
    start = source.index("ClassMethod BulkIngestEdgesSQL(")
    body = source[start : source.index("\nClassMethod ", start + 1)]
    insert = re.search(r"INSERT INTO Graph_KG\.rdf_edges \(([^)]*)\)", body)
    assert insert is not None, "BulkIngestEdgesSQL no longer inserts into rdf_edges"
    assert "graph_id" in insert.group(1), (
        "BulkIngestEdgesSQL omits graph_id from its INSERT, so it writes NULL "
        "where create_edge writes '' — two spellings of the default graph in one "
        f"table. Columns: {insert.group(1)}"
    )


# --- all_graphs is a distinct operation, not a key value ---------------------


def test_edge_scan_owns_the_cross_graph_delete():
    """`all_graphs=True` means every graph, which is not any single key value.

    Passing one derived key cannot express it. Expressing it in Python would mean
    enumerating `^KG("out")`'s graph subscripts from the wrong side of the seam,
    so the walk lives next to the delete it drives.
    """
    source = EDGE_SCAN.read_text(encoding="utf-8")
    assert "ClassMethod DeleteAdjacencyAllGraphs(" in source, (
        "EdgeScan has no cross-graph delete, so delete_edge(all_graphs=True) has "
        "no way to reach the adjacency it orphans."
    )
    start = source.index("ClassMethod DeleteAdjacencyAllGraphs(")
    body = source[start : source.index("\nClassMethod ", start + 1)]
    assert "..DeleteAdjacency(" in body, (
        "DeleteAdjacencyAllGraphs restates the per-edge invariant instead of "
        "delegating to DeleteAdjacency."
    )
    for bad in ('Kill ^KG("out"', 'Kill ^KG("deg"', 'Kill ^KG("degp"'):
        assert bad not in body, (
            f"DeleteAdjacencyAllGraphs contains `{bad}`, taking ownership of a "
            "store DeleteAdjacency already owns."
        )
