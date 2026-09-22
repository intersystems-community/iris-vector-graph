"""Spec 230 US1 (FR-003, FR-005) — the bulk loader can write a named graph.

`BulkLoader` had no graph at all. Every INSERT it issued omitted `graph_id`, so a
bulk load landed in whichever spelling the column default happened to be, and its
docstring said so as though it were a design choice: "Scoped to the default graph
because that is the only graph this loader writes". That makes the fastest write path
in the library the one path a tenant cannot use — and on a database upgraded rather
than created, where `graph_id` is still nullable with no default, it is also the path
that reintroduces the NULL spelling by the million.

Two properties, both readable off the emitted SQL:

- every insert names `graph_id` and binds the loader's graph (FR-003);
- the existence scans that make a re-load idempotent see a row written as NULL and a
  row written as `''` as the same default graph, because a bulk load that cannot see
  the previous one writes a second copy of it (FR-005).

A `graph` belongs to the loader, not to each call: one bulk load is one graph's worth
of data, and a per-call parameter would let a single dedupe scan and the inserts it
guards disagree about which graph they are in.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.bulk_loader import BulkLoader

GRAPH = "tenant-a"

NODES = [("n1", {"namespace": "Thing", "size": 3})]
EDGES = [("n1", "KNOWS", "n2", {"w": 1})]


def _loader(graph=None):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    loader = BulkLoader(conn, graph=graph) if graph is not None else BulkLoader(conn)
    return loader, cursor


def _statements(cursor):
    """Every (sql, params) the loader issued, `execute` and `executemany` alike."""
    out = []
    for call in cursor.execute.call_args_list:
        out.append((call.args[0], list(call.args[1]) if len(call.args) > 1 else []))
    for call in cursor.executemany.call_args_list:
        rows = list(call.args[1]) if len(call.args) > 1 else []
        out.append((call.args[0], rows))
    return out


def _inserts(cursor, table):
    found = [
        (sql, params)
        for sql, params in _statements(cursor)
        if sql.lstrip().upper().startswith("INSERT") and f".{table} " in sql
    ]
    assert found, f"the loader issued no INSERT into {table}: {[s for s, _ in _statements(cursor)]}"
    return found


def _columns(sql):
    match = re.search(r"\(([^)]*)\)\s*VALUES", sql, re.IGNORECASE)
    assert match, f"cannot read the column list out of: {sql}"
    return [c.strip().strip('"') for c in match.group(1).split(",")]


TABLES = ["nodes", "rdf_labels", "rdf_props", "rdf_edges"]


@pytest.mark.parametrize("table", TABLES)
def test_every_insert_names_the_graph_column(table):
    loader, cursor = _loader(GRAPH)

    loader.load_nodes(NODES)
    loader.load_edges(EDGES)

    for sql, rows in _inserts(cursor, table):
        columns = _columns(sql)
        assert "graph_id" in columns, (
            f"the {table} insert omits graph_id, so the row takes the column default "
            f"rather than the loader's graph: {sql}"
        )
        position = columns.index("graph_id")
        for row in rows:
            assert row[position] == GRAPH, (
                f"the {table} insert names graph_id but writes {row[position]!r}: {row}"
            )


def test_the_default_graph_is_written_explicitly():
    """`''`, named, rather than left to whatever the column default currently is.

    An upgraded database has `graph_id` nullable with no default until
    `tighten_graph_id_column` has run. On that database an omitted column is a NULL,
    and a NULL is the half of the default graph most predicates cannot see.
    """
    loader, cursor = _loader()

    loader.load_edges(EDGES)

    sql, rows = _inserts(cursor, "rdf_edges")[0]
    position = _columns(sql).index("graph_id")
    assert rows and rows[0][position] == "", f"the default graph was not written: {rows}"


@pytest.mark.parametrize("table", ["nodes", "rdf_edges"])
def test_the_existence_scans_are_scoped_to_the_loaders_graph(table):
    loader, cursor = _loader(GRAPH)

    loader.load_nodes(NODES)
    loader.load_edges(EDGES)

    scans = [
        (sql, params)
        for sql, params in _statements(cursor)
        if sql.lstrip().upper().startswith("SELECT") and f".{table} " in sql
    ]
    assert scans, f"nothing scanned {table} for existing rows"
    for sql, params in scans:
        assert "graph_id" in sql, (
            f"the {table} existence scan carries no graph predicate, so a re-load "
            f"reads another graph's rows as its own: {sql}"
        )
        assert GRAPH in params, f"the scan names a graph but binds {params}: {sql}"


@pytest.mark.parametrize("table", ["nodes", "rdf_edges"])
def test_dedupe_reads_the_null_and_empty_spellings_as_one_graph(table):
    """FR-005. A row a scan cannot see is a row the load writes a second copy of."""
    loader, cursor = _loader()

    loader.load_nodes(NODES)
    loader.load_edges(EDGES)

    scans = [
        sql
        for sql, _ in _statements(cursor)
        if sql.lstrip().upper().startswith("SELECT") and f".{table} " in sql
    ]
    assert scans, f"nothing scanned {table} for existing rows"
    for sql in scans:
        assert "COALESCE(graph_id, '')" in sql, (
            f"the {table} dedupe scan compares the stored graph_id directly, so a row "
            f"written as NULL and a row written as '' are different graphs: {sql}"
        )
