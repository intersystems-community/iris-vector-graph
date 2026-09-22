"""`create_node(graph=...)` has to put the labels and properties there too (FR-034).

The sibling of tests/unit/test_227_cypher_child_scope.py, on the engine path
rather than the Cypher one. `create_node` has taken a `graph` since spec 214, and
it reaches exactly one of the three statements it issues: the `nodes` INSERT
binds `graph_id`, while the `rdf_labels` and `rdf_props` INSERTs name neither the
column nor the value. Before T016 that was invisible — those tables had no such
column. After it, `create_node("x", labels=["Patient"], graph="A")` puts the node
in A and its label in the default graph, so the scoped label read of A finds
nothing and the default graph acquires a label for a node it does not hold.

The property INSERT carries a second defect: its `WHERE NOT EXISTS` guard is
keyed on `(s, "key")` alone, so graph B's `side` property suppresses the write of
graph A's `side` — and the caller is told the node was created.
"""

from unittest.mock import MagicMock

from iris_vector_graph.engine import IRISGraphEngine

GRAPH = "ivg227-children-A"


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


def _many(cursor, table: str):
    """Every `executemany` whose statement targets `table`, as (sql, rows)."""
    out = []
    for call in cursor.executemany.call_args_list:
        if not call.args:
            continue
        sql = " ".join(str(call.args[0]).split())
        if table in sql:
            out.append((sql, [list(r) for r in call.args[1]]))
    return out


def test_the_label_insert_names_the_graph():
    engine, cursor = _engine()

    engine.create_node("ivg227:x", labels=["Patient"], graph=GRAPH)

    inserts = _many(cursor, "rdf_labels")
    assert inserts, "no rdf_labels insert was issued, so this test proves nothing"
    for sql, rows in inserts:
        assert "graph_id" in sql, (
            f"the label insert names no graph, so the node goes to {GRAPH} and its "
            f"label to the default graph: {sql}"
        )
        assert all(GRAPH in row for row in rows), (
            f"the label insert names graph_id but does not bind the graph: {rows}"
        )


def test_the_property_insert_names_the_graph():
    engine, cursor = _engine()

    engine.create_node("ivg227:x", properties={"side": "a"}, graph=GRAPH)

    inserts = _many(cursor, "rdf_props")
    assert inserts, "no rdf_props insert was issued, so this test proves nothing"
    for sql, rows in inserts:
        assert "graph_id" in sql, f"the property insert names no graph: {sql}"
        assert all(GRAPH in row for row in rows), (
            f"the property insert names graph_id but does not bind the graph: {rows}"
        )


def test_the_property_guard_is_scoped():
    """The guard decides whether the row is written at all. Unscoped, the other
    graph's property of the same name suppresses this one, and `create_node`
    returns True."""
    engine, cursor = _engine()

    engine.create_node("ivg227:x", properties={"side": "a"}, graph=GRAPH)

    (sql, rows) = _many(cursor, "rdf_props")[0]
    guard = sql[sql.index("NOT EXISTS") :]
    assert "graph_id" in guard, (
        f"the idempotency guard spans every graph, so a property held in another "
        f"graph stops this one being written: {guard}"
    )


def test_an_omitted_graph_writes_the_default_graph():
    """`graph=None` is the default graph — never "any graph", and never NULL.

    A NULL `graph_id` on a child row is worse than a wrong one: `NULL = ''` is
    unknown, so the row is invisible to both the default graph's reads and every
    named graph's reads, and it cannot be found again without a COALESCE nobody
    wrote.
    """
    engine, cursor = _engine()

    engine.create_node("ivg227:x", labels=["Patient"], properties={"side": "a"})

    for table in ("rdf_labels", "rdf_props"):
        for sql, rows in _many(cursor, table):
            for row in rows:
                assert None not in row, (
                    f"an omitted graph binds NULL into {table}, which no scoped read "
                    f"can find: {row}"
                )
