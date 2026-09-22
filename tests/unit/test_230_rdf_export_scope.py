"""Spec 230 US1 (FR-001c) — the RDF export reads one graph's triples.

`_build_rdflib_graph` scoped `rdf_edges` on `graph_id` and scoped nothing else. Both
`rdf_labels` and `rdf_props` carry a `graph_id` column — `PRIMARY KEY (graph_id, s,
label)` and `(graph_id, s, key)` respectively — and both were read with a bare
`WHERE 1=1`. So `export_rdf(graph_id="tenant-a")` wrote tenant-a's edges alongside
every graph's `rdf:type` and literal triples, into a file the caller then hands to
someone. `validate_shacl` builds the same graph and inherits the same reach.

Two defects in one branch:

- a named graph leaked labels and properties, even though the caller named a graph;
- a falsy graph leaked everything, because `if graph_id:` cannot tell `""` — the
  default graph — from `None`.

`None` still means every graph, which is what `export_rdf`'s docstring has always
promised and what an export tool should default to. `""` means the default graph, as
it does everywhere else in IVG.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("rdflib", reason="the RDF export requires the [rdf] extra")

from iris_vector_graph._engine._rdf_utils import _build_rdflib_graph


def _statements(graph_id):
    """Every (sql, params) `_build_rdflib_graph` issues for one `graph_id`."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchmany.return_value = []
    cursor.fetchall.return_value = []

    _build_rdflib_graph(conn, graph_id=graph_id)

    out = []
    for call in cursor.execute.call_args_list:
        sql = call.args[0]
        params = list(call.args[1]) if len(call.args) > 1 else []
        out.append((sql, params))
    return out


def _for_table(statements, table):
    matches = [(sql, params) for sql, params in statements if table in sql]
    assert matches, f"no statement read {table}: {[s for s, _ in statements]}"
    assert len(matches) == 1, f"{table} was read {len(matches)} times"
    return matches[0]


TABLES = ["rdf_labels", "rdf_props", "rdf_edges"]


@pytest.mark.parametrize("table", TABLES)
def test_a_named_graph_scopes_every_table(table):
    sql, params = _for_table(_statements("tenant-a"), table)

    assert "graph_id = ?" in sql, (
        f"the {table} read carries no graph predicate:\n  {sql}\n"
        "Exporting one graph wrote every graph's triples into the caller's file."
    )
    assert "tenant-a" in params, (
        f"the {table} read names a graph but does not bind it: {params}"
    )


@pytest.mark.parametrize("table", TABLES)
def test_the_default_graph_is_a_graph_and_not_everything(table):
    """`graph_id=""` is the default graph, never a missing filter."""
    sql, _ = _for_table(_statements(""), table)

    assert "graph_id" in sql, (
        f"the {table} read is unscoped for graph_id='':\n  {sql}\n"
        "A caller asking for the default graph received every graph."
    )
    assert "COALESCE(graph_id, '') = ''" in sql, (
        "the default-graph predicate must match rows written as NULL and as '' — "
        f"got: {sql}"
    )


@pytest.mark.parametrize("table", TABLES)
def test_no_graph_at_all_still_exports_every_graph(table):
    """`graph_id=None` keeps its documented meaning: the whole namespace."""
    sql, params = _for_table(_statements(None), table)

    assert "graph_id = ?" not in sql and "COALESCE(graph_id" not in sql, (
        f"graph_id=None narrowed the {table} read, which export_rdf documents as "
        f"exporting every graph:\n  {sql}"
    )
    assert params == [], f"an unscoped read bound {params}"
