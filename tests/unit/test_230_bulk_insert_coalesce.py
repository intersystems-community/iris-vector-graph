"""Spec 230 US1 (FR-005) — bulk dedupe reads the two default-graph spellings as one.

`GraphSchema.get_bulk_insert_sql` is a table of hand-written INSERTs, each of the
shape `INSERT ... SELECT ? WHERE NOT EXISTS (<guard>)`. The guard is the whole
point: it is what makes a re-load idempotent. Three of them compared the stored
`graph_id` directly —

    WHERE graph_id = ? AND s = ? AND label = ?

— and on a database upgraded rather than created, half the default graph is stored
as NULL. `graph_id = ''` never matches a NULL, so the guard reports "nothing there"
and the load writes a second copy of every row it already has. The fourth,
`rdf_edges_with_graph`, spelled the same idea as

    (graph_id = ? OR (graph_id IS NULL AND ? IS NULL))

which takes two parameters to express and still misses a NULL-stored row whenever
the caller passes `''` — which is exactly what the default graph is called
everywhere else in IVG. And the unscoped `rdf_edges` template wrote `graph_id = ''`
into the row while guarding with no graph predicate at all, so a default-graph
load deduped against every graph in the namespace.

`COALESCE(graph_id, '') = COALESCE(?, '')` says all of it once, in one parameter.

The three unscoped child templates (`nodes`, `rdf_labels`, `rdf_props`) are the
pre-214 fallback for a schema that has no `graph_id` column at all — naming the
column there is SQLCODE -29 — so they are asserted to stay unscoped rather than
swept up with the rest.
"""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.schema import GraphSchema

#: Templates whose guard has to see both spellings of the default graph.
SCOPED = [
    "nodes_with_graph",
    "rdf_labels_with_graph",
    "rdf_props_with_graph",
    "rdf_edges_with_graph",
    "kg_NodeEmbeddings",
]

#: Templates for a schema that has no `graph_id` column to name.
UNSCOPED_FALLBACKS = ["nodes", "rdf_labels", "rdf_props"]

GRAPH = "ivg230-tenant-A"


def _guard(sql: str) -> str:
    assert "NOT EXISTS" in sql, f"the template has no dedupe guard at all:\n{sql}"
    return " ".join(sql[sql.index("NOT EXISTS") :].split())


@pytest.mark.parametrize("table", SCOPED)
def test_the_guard_coalesces_both_sides(table):
    guard = _guard(GraphSchema.get_bulk_insert_sql(table))

    assert "COALESCE(graph_id, '')" in guard, (
        f"the {table} guard compares the stored graph_id directly, so a row stored "
        f"as NULL is invisible to a caller passing '': {guard}"
    )
    assert "COALESCE(?, '')" in guard, (
        f"the {table} guard coalesces the stored side only, so a caller passing "
        f"None is looking for the literal NULL graph: {guard}"
    )


@pytest.mark.parametrize("table", SCOPED)
def test_the_guard_does_not_spell_the_default_graph_twice(table):
    """One parameter for the graph, not a `graph_id = ? OR ... ? IS NULL` pair.

    The two-parameter form is not merely verbose: the caller has to bind the graph
    twice in the right positions, and `nodes_edges.py` bound it twice in one branch
    and not at all in another.
    """
    guard = _guard(GraphSchema.get_bulk_insert_sql(table))

    assert "IS NULL" not in guard.upper(), (
        f"the {table} guard still tests for the NULL spelling separately: {guard}"
    )


def test_the_unscoped_edge_template_guards_the_default_graph():
    """It writes `graph_id = ''` into the row, so its guard must read that graph.

    Unscoped, a default-graph load deduped against every graph in the namespace:
    a tenant that already holds `(s, p, o)` made the default graph's copy of that
    edge silently unwritable.
    """
    sql = GraphSchema.get_bulk_insert_sql("rdf_edges")

    assert "COALESCE(graph_id, '') = ''" in _guard(sql), (
        f"the unscoped edge guard carries no graph predicate while the row it "
        f"guards names a graph:\n{sql}"
    )


@pytest.mark.parametrize("table", UNSCOPED_FALLBACKS)
def test_the_pre_214_fallbacks_name_no_graph(table):
    """These exist for a schema with no `graph_id` column; naming it is -29."""
    sql = GraphSchema.get_bulk_insert_sql(table)

    assert "graph_id" not in sql, (
        f"the {table} fallback names graph_id, so the one schema it exists to "
        f"serve — a pre-214 database without the column — now fails:\n{sql}"
    )


# ---------------------------------------------------------------------------
# The callers bind what the templates ask for
# ---------------------------------------------------------------------------


def _engine():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = (4,)
    cursor.fetchall.return_value = []
    cursor.description = []
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    cursor.execute.reset_mock()
    cursor.executemany.reset_mock()
    return engine, cursor


def _bound(cursor) -> list[tuple[str, list]]:
    """Every (sql, one row of params) the engine bound, both execute forms."""
    out = []
    for call in cursor.execute.call_args_list:
        if len(call.args) > 1 and isinstance(call.args[1], (list, tuple)):
            out.append((str(call.args[0]), list(call.args[1])))
    for call in cursor.executemany.call_args_list:
        if len(call.args) > 1:
            for row in call.args[1]:
                out.append((str(call.args[0]), list(row)))
    return out


def _markers(sql: str) -> int:
    """`?` count, ignoring any inside a quoted literal."""
    return len(re.findall(r"\?", re.sub(r"'[^']*'", "''", sql)))


def test_the_node_bulk_path_binds_one_parameter_per_marker():
    engine, cursor = _engine()

    engine.bulk_create_nodes(
        [{"id": "shared-1", "labels": ["Patient"], "properties": {"side": "l"}, "graph": GRAPH}],
        disable_indexes=False,
    )

    bound = [(sql, params) for sql, params in _bound(cursor) if "INSERT" in sql.upper()]
    assert bound, "the bulk node path bound nothing, so this test proves nothing"
    for sql, params in bound:
        assert len(params) == _markers(sql), (
            f"the template takes {_markers(sql)} parameters and the caller bound "
            f"{len(params)}: {params}\n{sql}"
        )


def test_the_edge_bulk_path_binds_one_parameter_per_marker():
    """`rdf_edges_with_graph` lost a parameter when its guard stopped spelling the
    default graph twice, and the caller bound the graph three times."""
    engine, cursor = _engine()

    engine.bulk_create_edges(
        [
            {"source_id": "a", "predicate": "KNOWS", "target_id": "b", "graph": GRAPH},
            {"source_id": "c", "predicate": "KNOWS", "target_id": "d"},
        ],
        disable_indexes=False,
        auto_sync=False,
    )

    bound = [
        (sql, params)
        for sql, params in _bound(cursor)
        if "INSERT" in sql.upper() and "rdf_edges" in sql
    ]
    assert bound, "the bulk edge path bound nothing, so this test proves nothing"
    for sql, params in bound:
        assert len(params) == _markers(sql), (
            f"the template takes {_markers(sql)} parameters and the caller bound "
            f"{len(params)}: {params}\n{sql}"
        )


def test_every_edge_in_a_mixed_batch_is_counted():
    """The mixed-graph branch executed one row at a time and counted only the rows
    that named a graph, so a batch holding both kinds under-reported itself."""
    engine, cursor = _engine()

    written = engine.bulk_create_edges(
        [
            {"source_id": "a", "predicate": "KNOWS", "target_id": "b", "graph": GRAPH},
            {"source_id": "c", "predicate": "KNOWS", "target_id": "d"},
        ],
        disable_indexes=False,
        auto_sync=False,
    )

    assert written == 2, (
        f"two edges were written and {written} reported; the default-graph edge in a "
        "mixed batch is not counted"
    )
