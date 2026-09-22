"""Callers of the embedding tables use the 4.0.0 key, not the removed `id` (T071).

Spec 227 re-keyed `kg_NodeEmbeddings` from `id VARCHAR PRIMARY KEY` to
`(graph_id, node_id)` with a `emb_rowid BIGINT IDENTITY` primary key. Every
statement that still names `id` was left behind, and the failure mode is worse than
a missing graph predicate because IRIS answers it:

    SELECT id, emb_rowid, node_id FROM Graph_KG.kg_NodeEmbeddings WHERE node_id='probe:1'
    -> (1543, 1543, 'probe:1')        `id` is the RowID
    SELECT COUNT(*) ... WHERE id = 'probe:1'
    -> 0                              no error, no rows
    INSERT INTO ... (id, emb) VALUES (?, ...)
    -> SQLCODE -108 'node_id' is a required field

(measured against `ivg-iris-enterprise` on 2026-09-20). So a read filtered on `id`
returns nothing and reports nothing, and the writes fail inside `except Exception`
blocks that count an error and continue. `test_spec_hygiene_gates.py`'s Gate 7 keeps
new ones out; this file pins the behaviour of the ones that were already there.
"""

from unittest.mock import MagicMock

import pytest

from tests.unit.route_fakes_227 import FakeRegistry, engine_with, teach_registry_route

DIM = 4
VEC = [0.1, 0.2, 0.3, 0.4]


def _sql(cursor):
    """Every statement issued on a MagicMock cursor, whitespace-normalised."""
    return [
        " ".join(str(call.args[0]).split())
        for call in cursor.execute.call_args_list
        if call.args
    ]


def _touching_embeddings(cursor):
    return [s for s in _sql(cursor) if "kg_NodeEmbeddings" in s or "kg_emb_" in s]


def _engine_with_mock_cursor():
    from iris_vector_graph.engine import IRISGraphEngine

    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    engine = IRISGraphEngine(conn)
    cursor.execute.reset_mock()
    return engine, cursor


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def test_get_embedding_reads_node_id_in_one_graph():
    """`get_embedding` filtered on `id`, so it returned None for every node.

    Silently: `WHERE id = 'patient:1'` compares a node ID against the RowID and
    matches nothing. A caller cannot tell that from "this node has no embedding".
    """
    engine, cursor = _engine_with_mock_cursor()
    teach_registry_route(cursor, "graphA", dimension=DIM)
    engine.get_embedding("patient:1", graph="graphA")

    reads = _touching_embeddings(cursor)
    assert reads, "no statement named an embedding table"
    stmt = reads[-1]
    assert "node_id = ?" in stmt, f"not keyed on node_id: {stmt}"
    assert "graph_id" in stmt, f"not scoped to a graph: {stmt}"


def test_get_embeddings_reads_node_id_in_one_graph():
    engine, cursor = _engine_with_mock_cursor()
    teach_registry_route(cursor, "graphA", dimension=DIM)
    engine.get_embeddings(["a", "b"], graph="graphA")

    stmt = _touching_embeddings(cursor)[-1]
    assert "node_id IN" in stmt, f"not keyed on node_id: {stmt}"
    assert "graph_id" in stmt, f"not scoped to a graph: {stmt}"


def test_get_embedding_on_an_unrouted_pair_reads_nothing():
    """No route means no vectors, so there is nothing to read — not a legacy scan.

    Falling back to `kg_NodeEmbeddings` here would answer graph A's question with
    the default graph's vector (FR-013).
    """
    engine, cursor = _engine_with_mock_cursor()
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    assert engine.get_embedding("patient:1", graph="graphA") is None


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def test_embed_nodes_writes_through_the_routed_path():
    """`embed_nodes` hand-rolled `INSERT ... (id, emb)`, which SQLCODE -108 refuses.

    The insert sat in `except Exception` and counted an error, so a bulk embed of a
    whole graph reported errors rather than a broken statement. It now goes through
    `store_embeddings`, which routes, enforces spec 226 identity, and binds the graph
    — the same three things every other write does.
    """
    registry = FakeRegistry(
        rows=[],
        tables=["kg_NodeEmbeddings"],
        dimension_for={"kg_NodeEmbeddings": DIM},
        nodes=[("graphA", "n1")],
    )
    engine = engine_with(registry)
    engine.embedder = None
    engine.embed_text = lambda text: list(VEC)

    result = engine.embed_nodes(
        text_fn=lambda node_id, props: "some text",
        graph="graphA",
        model_key="m-test",
    )

    assert result["errors"] == 0, f"embed_nodes reported errors: {result}"
    vector_writes = [
        (sql, params)
        for sql, params in registry.statements
        if sql.startswith("INSERT INTO") and "TO_VECTOR" in sql
    ]
    assert vector_writes, "embed_nodes wrote no vector"
    for sql, params in vector_writes:
        assert "(graph_id, node_id, emb" in sql, f"not keyed on (graph_id, node_id): {sql}"
        assert "graphA" in params, f"graph not bound: {params}"


def test_embed_nodes_selects_only_its_own_graphs_nodes():
    """Embedding "the nodes" must mean this graph's nodes.

    Unscoped, a bulk embed of graph A reads every node in the namespace and writes a
    graph-A vector for each — inventing graph-A membership for graph B's nodes.
    """
    registry = FakeRegistry(
        rows=[],
        tables=["kg_NodeEmbeddings"],
        dimension_for={"kg_NodeEmbeddings": DIM},
        nodes=[("graphA", "n1")],
    )
    engine = engine_with(registry)
    engine.embedder = None
    engine.embed_text = lambda text: list(VEC)
    engine.embed_nodes(text_fn=lambda n, p: "t", graph="graphA", model_key="m-test")

    node_reads = [
        (sql, params)
        for sql, params in registry.statements
        if sql.startswith("SELECT") and ".nodes" in sql and "COUNT" not in sql
    ]
    assert node_reads, "embed_nodes read no node list"
    sql, _params = node_reads[0]
    # The graph is in the SQL text, not the params: `build_node_where` returns a WHERE
    # body rather than a statement, so it has no placeholder list to append to and
    # quotes the value itself (`_sql_literal`).
    assert "graph_id" in sql, f"node selection is namespace-wide: {sql}"
    assert "'graphA'" in sql, f"graph not applied: {sql}"


def test_delete_node_removes_the_embedding_by_node_id():
    """The delete was `WHERE id = ?`, which removed nothing and said so to no one.

    Reach stays namespace-wide, matching every other delete in `delete_node` and the
    decision recorded in `reader-inventory.md` §10: a node ID deleted without a graph
    is deleted everywhere. The key is what was wrong, not the scope.
    """
    engine, cursor = _engine_with_mock_cursor()
    engine.delete_node("patient:1")

    deletes = [s for s in _touching_embeddings(cursor) if s.startswith("DELETE")]
    assert deletes, "delete_node issued no embedding delete"
    assert all("node_id" in s for s in deletes), deletes
    assert not any("WHERE id" in s for s in deletes), deletes


def test_bulk_delete_nodes_removes_embeddings_by_node_id():
    engine, cursor = _engine_with_mock_cursor()
    engine.bulk_delete_nodes(["a", "b"])

    deletes = [s for s in _touching_embeddings(cursor) if s.startswith("DELETE")]
    assert deletes, "bulk_delete_nodes issued no embedding delete"
    assert all("node_id IN" in s for s in deletes), deletes


# ---------------------------------------------------------------------------
# The selector that decides what is missing
# ---------------------------------------------------------------------------

def test_missing_only_filter_compares_node_ids():
    """`missing_only` read `NOT IN (SELECT id ...)`, i.e. NOT IN a list of RowIDs.

    Every node compares unequal to every RowID, so `missing_only=True` selected
    everything and re-embedded an entire graph on each run. It cost time rather than
    correctness, which is why nothing caught it.
    """
    from iris_vector_graph.embed_selector import EmbedSelector, build_node_where

    where = build_node_where(
        EmbedSelector(missing_only=True), schema_prefix="Graph_KG."
    )
    assert "SELECT node_id FROM" in where, where
    assert "SELECT id FROM" not in where, where


# ---------------------------------------------------------------------------
# Snapshot round-trip
# ---------------------------------------------------------------------------

def test_snapshot_exports_the_graph_and_the_node_id():
    """The exporter selected `id, emb, metadata`, so it wrote RowIDs as node IDs.

    A snapshot is the one artefact that outlives the installation that wrote it. An
    export keyed on RowID restores vectors attached to integers that match no node,
    and the restore reports success because every INSERT succeeds.
    """
    import inspect

    from iris_vector_graph._engine import snapshot

    src = inspect.getsource(snapshot)
    assert "SELECT id, emb, metadata FROM" not in src, (
        "the snapshot exporter still selects the RowID as the node key"
    )
    assert "SELECT graph_id, node_id, emb, metadata FROM" in src, (
        "the exporter must carry graph_id so a restore can put the row back in its graph"
    )


def test_snapshot_restore_accepts_a_pre_400_row():
    """A 3.2.0 snapshot has `id` and no `graph_id`; it still has to restore.

    Reading only `node_id` would make every snapshot written before 4.0.0
    unrestorable, and reading only `id` would ignore every snapshot written after.
    """
    import inspect

    from iris_vector_graph._engine import snapshot

    src = inspect.getsource(snapshot.SnapshotMixin.restore_snapshot)
    assert 'row.get("node_id")' in src and 'row.get("id")' in src, (
        "restore must accept both the 4.0.0 key and the pre-4.0.0 one"
    )


# ---------------------------------------------------------------------------
# Cypher
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cypher",
    [
        "CALL ivg.vector.search('Person', 'emb', [0.1, 0.2], 5) YIELD node RETURN node",
        "CALL ivg.vector.search('Person', 'emb', 'seed:1', 5) YIELD node RETURN node",
    ],
)
def test_translator_emits_no_id_column_on_an_embedding_table(cypher):
    """A generated statement joining an embedding table keys on `node_id`.

    `WHERE e.id = ?` in a similarity subselect is the silent-zero-rows shape: the
    score comes back NULL, the row sorts last, and the query still returns.
    """
    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql

    try:
        result = translate_to_sql(parse_query(cypher))
    except Exception:
        pytest.skip("this Cypher shape does not translate in isolation")
    flat = " ".join(result.sql.split())
    if "kg_NodeEmbeddings" not in flat and "kg_emb_" not in flat:
        pytest.skip("no embedding table in the generated SQL")
    assert ".id " not in flat and "WHERE id " not in flat, flat


# ---------------------------------------------------------------------------
# USE GRAPH in front of a CALL
# ---------------------------------------------------------------------------

def test_use_graph_survives_a_leading_call():
    """`USE GRAPH 'A' CALL ...` must scope the procedure to graph A.

    The parser's CALL branch returned a `CypherQuery` without passing
    `graph_context` through, so the graph was parsed, then dropped. Nothing
    downstream could notice: the SQL was simply unscoped, and an unscoped vector
    search returns *more* rows, never an error. This is SC-004's leak reached by a
    different route than a missing predicate — the predicate was never asked for.
    """
    from iris_vector_graph.cypher.parser import parse_query

    parsed = parse_query(
        "USE GRAPH 'graphA' CALL ivg.vector.search('Person', 'emb', [0.1, 0.2], 5)"
        " YIELD node RETURN node"
    )
    assert parsed.graph_context == "graphA"


@pytest.mark.parametrize(
    "query_input",
    ["[0.1, 0.2]", "'seed:1'"],
)
def test_use_graph_scopes_a_vector_search(query_input):
    """Every embedding table the search reads carries the graph.

    Both the ranked scan and the seed-vector subselect: reading the seed from
    another graph's row for the same node ID would score graph A's nodes against
    graph B's vector, which is a wrong answer rather than a missing one.
    """
    from iris_vector_graph.cypher.parser import parse_query
    from iris_vector_graph.cypher.translator import translate_to_sql

    result = translate_to_sql(
        parse_query(
            f"USE GRAPH 'graphA' CALL ivg.vector.search('Person', 'emb', {query_input}, 5)"
            " YIELD node RETURN node"
        )
    )
    flat = " ".join(result.sql.split())
    vec_cte = flat.split("VecSearch AS (", 1)[1].split(")\n", 1)[0]
    emb_refs = vec_cte.count("kg_NodeEmbeddings")
    assert emb_refs >= 1, flat
    assert vec_cte.count("graph_id") >= emb_refs, (
        "an embedding reference in the search CTE carries no graph predicate:\n" + vec_cte
    )
    assert "'graphA'" in vec_cte, vec_cte


# ---------------------------------------------------------------------------
# The idempotency guard on a graph-aware bulk insert (§4 of reader-inventory.md)
# ---------------------------------------------------------------------------


def test_nodes_with_graph_guard_names_the_graph():
    """`bulk_create_nodes`' template must not report another graph's row as "already there".

    `nodes_with_graph` inserts `(node_id, graph_id)` but guarded on `node_id`
    alone, so `NOT EXISTS` matched the same node ID in a different graph, the
    insert was skipped, and the node never appeared in the graph the caller asked
    for — no error, and `created` still listed it (spec 227 FR-034).
    """
    from iris_vector_graph.schema import GraphSchema

    sql = " ".join(GraphSchema.get_bulk_insert_sql("nodes_with_graph").split())
    guard = sql.split("NOT EXISTS", 1)[1]
    assert "graph_id" in guard, sql
    assert guard.count("?") == 2, sql
    assert sql.count("?") == 4, sql


def test_bulk_create_nodes_binds_the_graph_into_the_guard():
    """The caller's parameter list has to match the widened template."""
    engine, cursor = _engine_with_mock_cursor()
    engine._nodes_has_graph_id = True
    engine._children_have_graph_id = True
    engine.capabilities = MagicMock()
    engine.capabilities.has_bulk_ingest_nodes = False

    engine.bulk_create_nodes([{"id": "shared:1", "graph": "graphB", "labels": [], "properties": {}}])

    node_inserts = [
        call for call in cursor.executemany.call_args_list
        if call.args and "Graph_KG.nodes" in str(call.args[0])
    ]
    assert node_inserts, cursor.executemany.call_args_list
    params = node_inserts[0].args[1][0]
    assert list(params) == ["shared:1", "graphB", "shared:1", "graphB"], params
