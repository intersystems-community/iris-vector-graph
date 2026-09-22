"""Spec 227 T033 — a stored vector records which graph it belongs to.

The write side is where the read side's predicate becomes either true or useless.
`kg_KNN_VEC` can carry a perfect `COALESCE(n.graph_id,'') = COALESCE(?,'')` and
still return nothing at all if `store_embedding` wrote the row with no graph, or
return another graph's neighbours if it wrote every row under `''`.

Three things are asserted here, all by looking at the SQL rather than the rows:

- the INSERT names `graph_id` and binds the graph asked for.
- the DELETE that makes the write idempotent is scoped. Unscoped, storing a
  vector for `patient:1` in graph B deletes graph A's vector for the same node
  ID — the same collision `UNIQUE (graph_id, node_id)` exists to allow.
- the existence check is scoped. A node ID present in graph B but not in graph A
  must not satisfy the check for a write into A, or the FK the routed tables
  declare is the only thing left catching it, and the legacy tables declare none.
"""

import re

import pytest
from unittest.mock import MagicMock

from iris_vector_graph.constants import DEFAULT_GRAPH
from iris_vector_graph.engine import IRISGraphEngine
from tests.unit.route_fakes_227 import teach_registry_route

GRAPH = "ivg227-store-A"


def _engine():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    # `_assert_node_exists` counts nodes; `_get_embedding_dimension` reads a width;
    # `get_embedding_identity` reads the registry. One non-zero scalar answers all
    # three in the shape each expects to see for "yes, and it is 4 wide".
    cursor.fetchone.return_value = (4,)
    cursor.fetchall.return_value = []
    cursor.description = []
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    # Routed, so the write goes to an existing route rather than creating one: these
    # assertions are about the statement a scoped write generates, not about the
    # first-write-creates-the-route path (`test_227_routed_writes.py` covers that).
    teach_registry_route(cursor, GRAPH)
    cursor.execute.reset_mock()
    return engine, cursor


def _statements(cursor):
    return [
        (" ".join(str(c.args[0]).split()), list(c.args[1]) if len(c.args) > 1 else [])
        for c in cursor.execute.call_args_list
        if c.args
    ]


def _matching(cursor, keyword: str):
    return [(s, p) for s, p in _statements(cursor) if s.upper().startswith(keyword)]


# --- store_embedding ------------------------------------------------------------


def test_the_insert_names_the_graph_column():
    eng, cursor = _engine()

    eng.store_embedding("patient:1", [0.1, 0.2, 0.3, 0.4], graph=GRAPH)

    inserts = _matching(cursor, "INSERT")
    assert inserts, "no INSERT was issued"
    sql, params = inserts[0]
    assert re.search(r"\(\s*graph_id\s*,\s*node_id\s*,", sql), (
        f"the INSERT does not name graph_id: {sql}"
    )
    assert GRAPH in params, f"the graph was not bound: {params}"


def test_the_idempotency_delete_is_scoped():
    """Unscoped, a write into B erases A's vector for the same node ID."""
    eng, cursor = _engine()

    eng.store_embedding("patient:1", [0.1, 0.2, 0.3, 0.4], graph=GRAPH)

    deletes = _matching(cursor, "DELETE")
    assert deletes, "no DELETE was issued, so the write is not idempotent"
    for sql, params in deletes:
        assert "COALESCE" in sql, f"the DELETE crosses graphs: {sql}"
        assert GRAPH in params, f"the DELETE did not bind the graph: {params}"


def test_the_existence_check_is_scoped():
    eng, cursor = _engine()

    eng.store_embedding("patient:1", [0.1, 0.2, 0.3, 0.4], graph=GRAPH)

    counts = [
        (s, p) for s, p in _statements(cursor)
        if "COUNT(*)" in s.upper() and "nodes" in s
    ]
    assert counts, "the node existence check did not run"
    for sql, params in counts:
        assert "COALESCE" in sql, (
            f"a node in another graph satisfies the check for this write: {sql}"
        )
        assert GRAPH in params, params


def test_an_omitted_graph_writes_the_default_graph():
    """Not NULL, and not "whatever the column default is".

    The column carries `DEFAULT ''`, so an omitted column would also land in the
    default graph — but only on a fresh install. On an upgraded install the column
    arrived by `ADD COLUMN` and is nullable, and a NULL there reads as the default
    graph only because every predicate COALESCEs it. Writing the value explicitly
    means the rows agree with each other regardless of which install they are in.
    """
    eng, cursor = _engine()

    eng.store_embedding("patient:1", [0.1, 0.2, 0.3, 0.4])

    sql, params = _matching(cursor, "INSERT")[0]
    assert "graph_id" in sql
    assert DEFAULT_GRAPH in params, params


def test_the_graph_is_keyword_only():
    """A 3.2.0 positional call must not bind a graph to `metadata` or `dtype`."""
    eng, _ = _engine()

    with pytest.raises(TypeError):
        eng.store_embedding("patient:1", [0.1, 0.2, 0.3, 0.4], None, "DOUBLE", GRAPH)


# --- store_embeddings -----------------------------------------------------------


def test_the_batch_insert_names_the_graph_for_every_row():
    eng, cursor = _engine()

    eng.store_embeddings(
        [
            {"node_id": "patient:1", "embedding": [0.1, 0.2, 0.3, 0.4]},
            {"node_id": "patient:2", "embedding": [0.5, 0.6, 0.7, 0.8]},
        ],
        graph=GRAPH,
    )

    inserts = _matching(cursor, "INSERT")
    assert len(inserts) == 2, f"expected one INSERT per item, got {len(inserts)}"
    for sql, params in inserts:
        assert "graph_id" in sql, sql
        assert GRAPH in params, params


def test_the_batch_delete_is_scoped_too():
    eng, cursor = _engine()

    eng.store_embeddings(
        [{"node_id": "patient:1", "embedding": [0.1, 0.2, 0.3, 0.4]}], graph=GRAPH
    )

    for sql, params in _matching(cursor, "DELETE"):
        assert "COALESCE" in sql, sql
        assert GRAPH in params, params


def test_a_batch_uses_one_graph_for_the_whole_batch():
    """Deliberately not per-item.

    A per-item graph would make a batch span routes, and a route is one physical
    table with one declared width — so a mixed batch could only be honoured by
    splitting it across tables and losing the single transaction that makes the
    batch worth using. One graph per call keeps "a refused batch writes nothing"
    true (FR-002).
    """
    eng, cursor = _engine()

    eng.store_embeddings(
        [
            {"node_id": "patient:1", "embedding": [0.1, 0.2, 0.3, 0.4], "graph": "ignored-B"},
            {"node_id": "patient:2", "embedding": [0.5, 0.6, 0.7, 0.8]},
        ],
        graph=GRAPH,
    )

    for sql, params in _matching(cursor, "INSERT"):
        assert "ignored-B" not in params, (
            f"a per-item graph key was honoured, splitting the batch: {params}"
        )
        assert GRAPH in params, params


# --- the unembedded-node reader -------------------------------------------------


def test_get_unembedded_nodes_joins_on_node_id_within_one_graph():
    """`e.id` no longer exists, and an unscoped join answers the wrong question.

    Without the graph on the join, a node embedded in graph B counts as embedded
    in graph A, so graph A's backfill silently skips it.
    """
    eng, cursor = _engine()

    eng.get_unembedded_nodes(graph=GRAPH)

    joins = [s for s, _ in _statements(cursor) if "LEFT JOIN" in s.upper()]
    assert joins, "no statement was issued"
    for sql in joins:
        assert not re.search(r"\be\.id\b", sql), (
            f"the join still uses the removed `id` column: {sql}"
        )
        assert "e.node_id" in sql, sql
        assert sql.upper().count("COALESCE") >= 2, (
            f"both sides of the join must be graph-scoped: {sql}"
        )


# --- the embed queue (T035, Python side) -----------------------------------------
#
# The .cls carries the graph subscript now, but the subscript is only reachable if
# Python passes a graph in and reads one back out. An enqueue that drops the graph
# writes `""` into the subscript and the worker then writes every vector into the
# default graph — the exact failure the subscript was added to prevent, with the
# storage layout looking correct the whole time.


def _queue_engine(monkeypatch, claim):
    """An engine whose `_call_classmethod` is recorded rather than executed."""
    calls = []

    def _fake(conn, cls, method, *args):
        calls.append((method, list(args)))
        if method == "ClaimPendingBatch":
            import json

            return json.dumps(claim)
        return "1"

    import iris_vector_graph.schema as _schema

    monkeypatch.setattr(_schema, "_call_classmethod", _fake)
    eng, cursor = _engine()
    eng.enforce_embedding_identity = MagicMock()
    return eng, cursor, calls


def test_enqueue_passes_the_graph_to_both_bulk_methods(monkeypatch):
    eng, _cursor, calls = _queue_engine(monkeypatch, [])

    eng.enqueue_for_embedding(node_ids=["patient:1"], texts=["hello"], graph=GRAPH)

    by_method = {m: a for m, a in calls}
    assert "BulkEnqueue" in by_method, calls
    assert "BulkEnqueueText" in by_method, calls
    assert GRAPH in by_method["BulkEnqueue"], (
        f"BulkEnqueue was called without the graph: {by_method['BulkEnqueue']}"
    )
    assert GRAPH in by_method["BulkEnqueueText"], (
        f"BulkEnqueueText was called without the graph: {by_method['BulkEnqueueText']}"
    )


def test_an_omitted_graph_enqueues_the_default_graph(monkeypatch):
    eng, _cursor, calls = _queue_engine(monkeypatch, [])

    eng.enqueue_for_embedding(node_ids=["patient:1"])

    args = dict(calls)["BulkEnqueue"]
    assert args[-1] == DEFAULT_GRAPH, (
        f"the default graph is not sent explicitly, so the .cls default decides "
        f"instead of this call: {args}"
    )


def test_the_queue_graph_is_keyword_only(monkeypatch):
    eng, _cursor, _calls = _queue_engine(monkeypatch, [])
    with pytest.raises(TypeError):
        eng.enqueue_for_embedding(["patient:1"], "", None, GRAPH)


def test_the_worker_writes_the_vector_into_the_entrys_graph(monkeypatch):
    """The claim returns the graph; the upsert must use it. Dropping it here is the
    whole bug: the enqueue was scoped, the subscript was stored, and the vector
    still lands in the default graph."""
    eng, cursor, _calls = _queue_engine(
        monkeypatch,
        [{"reqId": "patient:1", "text": "a", "node_id": "patient:1", "config": "", "graph": GRAPH}],
    )
    eng._encode_batch = MagicMock(return_value=[[0.1, 0.2, 0.3, 0.4]])

    eng.process_embed_queue()

    inserts = _matching(cursor, "INSERT")
    assert inserts, "the worker wrote no vector"
    sql, params = inserts[0]
    assert re.search(r"\(\s*graph_id\s*,\s*node_id\s*,", sql), (
        f"the worker's INSERT does not name graph_id: {sql}"
    )
    assert GRAPH in params, f"the entry's graph was not written: {params}"
    deletes = _matching(cursor, "DELETE")
    assert deletes, "no idempotency DELETE"
    assert "COALESCE" in deletes[0][0], (
        f"the worker's DELETE is unscoped, so it erases another graph's vector: "
        f"{deletes[0][0]}"
    )


def test_an_entry_with_no_graph_subscript_writes_the_default_graph(monkeypatch):
    """A pre-upgrade entry has no `graph` key at all. It means the default graph —
    not `None`, which would bind as SQL NULL and store a vector belonging to no
    graph that no scoped read can ever find."""
    eng, cursor, _calls = _queue_engine(
        monkeypatch,
        [{"reqId": "patient:1", "text": "a", "node_id": "patient:1", "config": ""}],
    )
    eng._encode_batch = MagicMock(return_value=[[0.1, 0.2, 0.3, 0.4]])

    eng.process_embed_queue()

    sql, params = _matching(cursor, "INSERT")[0]
    # By position: `graph_id` is the first column the INSERT names, and a trailing NULL
    # is the absent `metadata` — a vector with no metadata is ordinary, a vector with no
    # graph is not.
    assert re.search(r"\(\s*graph_id\s*,\s*node_id\s*,", sql), (
        f"the worker's INSERT does not name graph_id first: {sql}"
    )
    assert params[0] == DEFAULT_GRAPH, f"the graph is not the default graph: {params}"
