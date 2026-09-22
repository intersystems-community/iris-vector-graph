"""Spec 230 US2 (FR-018, FR-020) — every existing document is accounted for.

Re-keying `Graph_KG.docs` (FR-007) changes what `id` means: it was a document ID
chosen by whoever wrote the row, and it is now a node ID. An upgraded installation
therefore holds rows whose key may or may not name a node, and the migration is
the only place that can decide. It gets exactly three answers per row:

* the ID is in exactly one graph → place it there;
* the ID is in more than one → ask the resolver, and quarantine `ambiguous_graph`
  (or `resolver_declined`) if there is no answer;
* the ID is in no graph → quarantine `no_node`.

Defaulting to `''` is not on the list. A document about graph B's node that lands
in the default graph is a silent scope error, and it is the failure mode that made
this a 4.0.0 change rather than an `ALTER TABLE`.

These tests count rows against an in-memory stand-in
(`tests/unit/migration_fakes_230.py`). `tests/e2e/test_230_retrieval_scope.py`
proves the placement against IRIS.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.migrations.docs_and_edge_vectors import (
    AmbiguousDocument,
    DOCS_QUARANTINE_REASONS,
    place_documents,
)

from iris_vector_graph.migrations.kg_node_stores import (
    REKEYED_STORES,
    rekey_kg_node_stores,
)

from tests.unit.migration_fakes_230 import docs_conn, kg_stores_conn


def test_a_document_whose_node_is_in_one_graph_is_placed_there():
    conn = docs_conn(
        [{"id": "n1", "text": "alpha"}],
        {"n1": ["tenant-a"]},
    )

    report = place_documents(conn)

    assert conn.registry.placed() == {"n1": "tenant-a"}
    assert report.rows_placed == {"tenant-a": 1}
    assert report.rows_quarantined == {}


def test_the_default_graph_is_a_placement_like_any_other():
    """A node in `''` alone is unambiguous, so its document is placed, not skipped."""
    conn = docs_conn([{"id": "n1", "text": "alpha"}], {"n1": [""]})

    report = place_documents(conn)

    assert conn.registry.placed() == {"n1": ""}
    assert report.rows_placed == {"": 1}


def test_a_document_naming_no_node_is_quarantined_not_defaulted():
    conn = docs_conn([{"id": "doc-42", "text": "alpha"}], {})

    report = place_documents(conn)

    assert conn.registry.quarantined() == {"doc-42": "no_node"}
    assert report.rows_quarantined == {"no_node": 1}
    assert report.rows_placed == {}
    assert "" not in conn.registry.placed().values()


def test_an_ambiguous_document_is_quarantined_when_no_resolver_is_given():
    conn = docs_conn([{"id": "n1", "text": "alpha"}], {"n1": ["tenant-a", "tenant-b"]})

    report = place_documents(conn)

    assert report.rows_quarantined == {"ambiguous_graph": 1}
    assert conn.registry.quarantined() == {"n1": "ambiguous_graph"}


def test_a_resolver_places_an_ambiguous_document():
    seen: list[AmbiguousDocument] = []

    def resolver(ambiguous):
        seen.append(ambiguous)
        return "tenant-b"

    conn = docs_conn([{"id": "n1", "text": "alpha"}], {"n1": ["tenant-a", "tenant-b"]})

    report = place_documents(conn, resolver=resolver)

    assert conn.registry.placed() == {"n1": "tenant-b"}
    assert report.rows_placed == {"tenant-b": 1}
    assert [(a.doc_id, a.candidate_graphs) for a in seen] == [("n1", ("tenant-a", "tenant-b"))]


def test_a_resolver_naming_a_graph_that_does_not_hold_the_node_has_declined():
    """Honouring it would place the row somewhere nothing links it to — the exact
    silent misplacement the quarantine exists to prevent."""
    conn = docs_conn([{"id": "n1", "text": "alpha"}], {"n1": ["tenant-a", "tenant-b"]})

    report = place_documents(conn, resolver=lambda _: "tenant-z")

    assert conn.registry.quarantined() == {"n1": "resolver_declined"}
    assert report.rows_quarantined == {"resolver_declined": 1}


def test_a_resolver_that_declines_is_recorded_as_a_decline():
    conn = docs_conn([{"id": "n1", "text": "alpha"}], {"n1": ["tenant-a", "tenant-b"]})

    report = place_documents(conn, resolver=lambda _: None)

    assert report.rows_quarantined == {"resolver_declined": 1}


def test_a_resolver_is_not_consulted_about_an_unambiguous_document():
    """It has nothing to add, and asking would let a resolver move a row whose
    graph is already known."""
    asked = []
    conn = docs_conn([{"id": "n1", "text": "alpha"}], {"n1": ["tenant-a"]})

    place_documents(conn, resolver=lambda a: asked.append(a) or "tenant-b")

    assert asked == []
    assert conn.registry.placed() == {"n1": "tenant-a"}


def test_a_resolver_is_not_consulted_about_a_document_with_no_node():
    """There is no candidate to choose between; a resolver answer here would be an
    invention, and the row is preserved in quarantine for a human instead."""
    asked = []
    conn = docs_conn([{"id": "doc-42", "text": "alpha"}], {})

    report = place_documents(conn, resolver=lambda a: asked.append(a) or "tenant-a")

    assert asked == []
    assert report.rows_quarantined == {"no_node": 1}


def test_every_row_is_either_placed_or_quarantined():
    conn = docs_conn(
        [
            {"id": "n1", "text": "one"},
            {"id": "n2", "text": "two"},
            {"id": "n3", "text": "three"},
            {"id": "doc-42", "text": "orphan"},
            {"id": "n4", "text": "shared"},
        ],
        {
            "n1": ["tenant-a"],
            "n2": ["tenant-a"],
            "n3": ["tenant-b"],
            "n4": ["tenant-a", "tenant-b"],
        },
    )

    report = place_documents(conn)

    assert report.rows_accounted == 5, report
    assert report.rows_placed == {"tenant-a": 2, "tenant-b": 1}
    assert report.rows_quarantined == {"no_node": 1, "ambiguous_graph": 1}
    assert conn.registry.unplaced() == [], "a row was left with no graph at all"


def test_a_quarantined_row_keeps_its_text():
    """The row is moved, not summarised: a quarantine that drops the document is a
    delete with extra steps."""
    conn = docs_conn([{"id": "doc-42", "text": "the body"}], {})

    place_documents(conn)

    assert conn.registry.quarantine == [
        {"doc_id": "doc-42", "text": "the body", "reason": "no_node"}
    ]


def test_placed_rows_are_never_deleted():
    conn = docs_conn(
        [{"id": "n1", "text": "one"}, {"id": "n2", "text": "two"}],
        {"n1": ["tenant-a"], "n2": ["tenant-b"]},
    )

    place_documents(conn)

    assert conn.registry.deleted == []
    assert len(conn.registry.docs) == 2


def test_every_quarantine_reason_is_a_declared_one():
    """The reasons are a closed set shared with 227's vector quarantine, so a
    report can be read without guessing at strings."""
    conn = docs_conn(
        [{"id": "doc-42", "text": "x"}, {"id": "n1", "text": "y"}],
        {"n1": ["a", "b"]},
    )

    report = place_documents(conn)

    assert set(report.rows_quarantined) <= set(DOCS_QUARANTINE_REASONS), report.rows_quarantined


def test_a_second_run_changes_nothing():
    conn = docs_conn(
        [
            {"id": "n1", "text": "one"},
            {"id": "doc-42", "text": "orphan"},
            {"id": "n4", "text": "shared"},
        ],
        {"n1": ["tenant-a"], "n4": ["tenant-a", "tenant-b"]},
    )

    place_documents(conn)
    before = conn.registry.snapshot()

    second = place_documents(conn)

    assert conn.registry.snapshot() == before
    assert second.rows_accounted == 0, f"a re-run re-placed rows that were already placed: {second}"


def test_a_dry_run_reports_without_writing():
    conn = docs_conn(
        [{"id": "n1", "text": "one"}, {"id": "doc-42", "text": "orphan"}],
        {"n1": ["tenant-a"]},
    )

    report = place_documents(conn, dry_run=True)

    assert report.rows_placed == {"tenant-a": 1}
    assert report.rows_quarantined == {"no_node": 1}
    assert conn.registry.placed() == {"n1": None, "doc-42": None}
    assert conn.registry.quarantine == []


def test_an_install_without_the_column_is_refused_rather_than_half_migrated():
    """`graph_id` arrives with the schema upgrade. Placing rows before the column
    exists cannot work, and pretending it did would report a clean migration.
    """
    conn = docs_conn([{"id": "n1", "text": "one"}], {"n1": ["a"]}, has_graph_column=False)

    with pytest.raises(RuntimeError, match="graph_id"):
        place_documents(conn)


def test_a_resumed_run_after_the_rebuild_has_nothing_left_to_place():
    """`finish_docs` drops `docs` one statement before renaming the rebuilt table over
    it, so a pass killed in between leaves an install with no `docs` at all. Every row
    is already placed — it is sitting in the staging table — and refusing here would
    strand the install one statement from finished, with the operator's only clue an
    error about a column on a table that no longer exists.
    """
    conn = docs_conn([], {}, has_docs_table=False, has_staging_table=True)

    report = place_documents(conn)

    assert report.rows_accounted == 0
    assert report.rows_placed == {}
    assert report.rows_quarantined == {}


def test_a_missing_docs_table_with_no_rebuild_in_flight_is_refused():
    """No `docs` and no staging table is not a resumable migration — it is a namespace
    that never had the schema, and placing rows into it is not something to guess at."""
    conn = docs_conn([], {}, has_docs_table=False)

    with pytest.raises(RuntimeError, match="docs"):
        place_documents(conn)


# ---------------------------------------------------------------------------
# T044 / US3 — the ^KG rebuild (FR-011, FR-020)
# ---------------------------------------------------------------------------
#
# `^KG("prop")`, `^KG("label")`, `^KG("deg2p")` and `^KG("deg2p_exact")` gain a graph
# subscript, so every entry an upgraded install holds is in a layout no 4.0.0 reader
# addresses. They cannot be moved: the entry does not say which graph it belongs to,
# which is the whole defect. They are dropped, and the server rebuilds them from the
# SQL rows — each of which does carry a graph.
#
# The rows are the authority for a reason. A migration that instead read the flat entry
# and looked its node up in `Graph_KG.nodes` would be inferring the graph, and after
# 227 broke `UNIQUE (node_id)` that lookup can answer two graphs for one ID. FR-011
# closes the option: no entry's graph is inferred.


def _graph_rows():
    """Two graphs and the default graph, each with labels, properties and a two-hop
    path of its own."""
    return {
        "props": [
            {"graph_id": "a", "s": "n1", "key": "name", "val": "in a"},
            {"graph_id": "b", "s": "n1", "key": "name", "val": "in b"},
            {"graph_id": "", "s": "d1", "key": "name", "val": "default"},
        ],
        "labels": [
            {"graph_id": "a", "s": "n1", "label": "Thing"},
            {"graph_id": "b", "s": "n1", "label": "Thing"},
            {"graph_id": "", "s": "d1", "label": "Thing"},
        ],
        "edges": [
            {"graph_id": "a", "s": "n1", "p": "P", "o_id": "n2"},
            {"graph_id": "a", "s": "n2", "p": "P", "o_id": "n3"},
            {"graph_id": "b", "s": "n1", "p": "P", "o_id": "n9"},
            {"graph_id": "b", "s": "n9", "p": "P", "o_id": "n8"},
            {"graph_id": "", "s": "d1", "p": "P", "o_id": "d2"},
            {"graph_id": "", "s": "d2", "p": "P", "o_id": "d3"},
        ],
    }


#: What a 3.2.0 install holds: the same statistics, keyed with no graph at all.
FLAT_RESIDUE = {
    ("prop", "n1", "name"): "whichever graph wrote last",
    ("label", "Thing", "n1"): "",
    ("deg2p", "n1", "P"): 2,
    ("deg2p_exact", "n1", "P"): 2,
}


def test_the_kg_rebuild_is_idempotent():
    """Two runs leave the same tree and report the same counts. The step is a drop and
    a rebuild, so a second run is not a no-op internally — it does the same work again
    and has to land in the same place."""
    conn = kg_stores_conn(**_graph_rows(), flat_entries=dict(FLAT_RESIDUE))

    first = rekey_kg_node_stores(conn)
    after_first = conn.registry.snapshot()

    second = rekey_kg_node_stores(conn)

    assert conn.registry.snapshot() == after_first
    assert second.entries_rebuilt == first.entries_rebuilt


def test_every_rebuilt_entry_leads_with_a_graph_key():
    conn = kg_stores_conn(**_graph_rows(), flat_entries=dict(FLAT_RESIDUE))

    rekey_kg_node_stores(conn)

    known = {"0", "a", "b"}
    for store in REKEYED_STORES:
        for subscripts in conn.registry.entries(store):
            assert str(subscripts[0]) in known, (
                f'^KG("{store}", {subscripts[0]!r}, ...) does not lead with a graph '
                "key, so it is an entry in the pre-4.0.0 layout"
            )


def test_the_flat_residue_does_not_survive():
    """The entry that made this a migration rather than a schema change: `deg2p`
    counted across every graph, so the number itself belongs to no graph and cannot be
    carried forward."""
    conn = kg_stores_conn(**_graph_rows(), flat_entries=dict(FLAT_RESIDUE))

    report = rekey_kg_node_stores(conn)

    assert report.entries_dropped == len(FLAT_RESIDUE)
    for subscripts in FLAT_RESIDUE:
        assert subscripts not in conn.registry.tree, f"{subscripts} survived the rebuild"


def test_a_row_with_no_graph_becomes_a_default_graph_entry_not_an_inferred_one():
    """FR-011. A 3.2.0 `rdf_props` row has no `graph_id`, and 214's convention says a
    row with none is a default-graph row. The node ID also existing in graph `a` does
    not make it graph `a`'s property — inferring that is exactly what is forbidden."""
    rows = _graph_rows()
    rows["props"].append({"graph_id": None, "s": "n1", "key": "legacy", "val": "unscoped"})

    conn = kg_stores_conn(**rows)

    rekey_kg_node_stores(conn)

    entries = conn.registry.entries("prop")
    assert ("0", "n1", "legacy") in {tuple(map(str, k)) for k in entries}, (
        "a row with no graph_id did not land in the default graph"
    )
    assert ("a", "n1", "legacy") not in {tuple(map(str, k)) for k in entries}, (
        "the rebuild inferred a graph for a row that did not name one"
    )


def test_a_source_table_without_a_graph_column_is_refused_before_anything_is_dropped():
    """The rebuild reads the graph off the row. A source still on the pre-214 shape has
    no graph to read, so every entry would land in the default graph — and the drop
    would already have happened, leaving nothing to recover the real layout from."""
    conn = kg_stores_conn(
        **_graph_rows(),
        flat_entries=dict(FLAT_RESIDUE),
        scoped_columns=("rdf_labels", "rdf_edges"),
    )

    with pytest.raises(RuntimeError, match="graph_id"):
        rekey_kg_node_stores(conn)

    assert conn.registry.killed == [], "the refusal came after the kill"
    for subscripts in FLAT_RESIDUE:
        assert subscripts in conn.registry.tree


def test_the_report_counts_entries_per_graph():
    conn = kg_stores_conn(**_graph_rows())

    report = rekey_kg_node_stores(conn)

    assert set(report.entries_rebuilt) <= set(REKEYED_STORES)
    assert report.entries_rebuilt["prop"] == {"": 1, "a": 1, "b": 1}
    assert report.entries_rebuilt["label"] == {"": 1, "a": 1, "b": 1}
    assert set(report.entries_rebuilt["deg2p"]) == {"", "a", "b"}


def test_the_rebuild_is_the_servers_and_the_step_only_orders_it():
    """The layout has one writer. A migration that wrote entries itself would be a
    second spelling of the subscript order, and the fake refuses the write outright —
    so this asserts the positive: the server's own rebuild ran."""
    conn = kg_stores_conn(**_graph_rows())

    rekey_kg_node_stores(conn)

    assert "BuildKG" in conn.registry.rebuilds
    assert any(name.startswith("Build2Hop") for name in conn.registry.rebuilds)


def test_a_dry_run_reports_the_flat_residue_without_dropping_it():
    conn = kg_stores_conn(**_graph_rows(), flat_entries=dict(FLAT_RESIDUE))
    before = conn.registry.snapshot()

    report = rekey_kg_node_stores(conn, dry_run=True)

    assert report.entries_dropped == len(FLAT_RESIDUE)
    assert conn.registry.snapshot() == before
    assert conn.registry.killed == []
    assert conn.registry.rebuilds == []
