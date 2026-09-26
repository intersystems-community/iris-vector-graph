"""Scenario teardown must leave no rows behind (spec 229 FR-009).

Side effects are measured as graph-state deltas, and ``+labels`` counts label names
new to the graph, so a label left behind by one scenario hides the next scenario's
``+labels``. The Cypher ``DETACH DELETE`` teardown is not enough on its own: when it
leaves rows, the SQL sweep scoped to the isolation label must remove them.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock


def _ctx(ids=("n1", "n2")):
    cursor = MagicMock()
    cursor.fetchall.return_value = [(i,) for i in ids]
    conn = MagicMock()
    conn.cursor.return_value = cursor
    engine = MagicMock()
    engine._schema_prefix = "Graph_KG"
    engine._store = SimpleNamespace(conn=conn)
    return SimpleNamespace(engine=engine), cursor


def _deletes(cursor):
    return [
        (c.args[0], c.args[1] if len(c.args) > 1 else None)
        for c in cursor.execute.call_args_list
        if c.args[0].startswith("DELETE") and "?" in c.args[0]
    ]


def test_teardown_reads_the_label_scoped_ids_first():
    """Before the Cypher delete: it removes the isolation-label rows the ids come from."""
    from tests.tck.environment import _teardown_label

    ctx, cursor = _ctx()
    order = []
    cursor.execute.side_effect = lambda sql, *a: order.append(sql.split()[0])
    ctx.engine.execute_cypher.side_effect = lambda *a: order.append("CYPHER")
    _teardown_label(ctx, "TCK_abc")
    assert order[:2] == ["SELECT", "CYPHER"]
    assert order[2:] == ["DELETE"] * 4 + order[6:]
    select = next(c for c in cursor.execute.call_args_list if c.args[0].startswith("SELECT"))
    assert "rdf_labels" in select.args[0] and select.args[1] == ["TCK_abc"]


def test_teardown_sweeps_every_table_labels_before_nodes():
    from tests.tck.environment import _teardown_label

    ctx, cursor = _ctx()
    _teardown_label(ctx, "TCK_abc")
    tables = [sql.split("FROM ", 1)[1].split()[0] for sql, _ in _deletes(cursor)]
    assert tables == [
        "Graph_KG.rdf_edges",
        "Graph_KG.rdf_props",
        "Graph_KG.rdf_labels",
        "Graph_KG.nodes",
    ]
    for sql, params in _deletes(cursor):
        assert "n1" in params and "n2" in params


def test_teardown_edge_sweep_covers_both_endpoints():
    from tests.tck.environment import _teardown_label

    ctx, cursor = _ctx()
    _teardown_label(ctx, "TCK_abc")
    sql, params = next(d for d in _deletes(cursor) if "rdf_edges" in d[0])
    assert " s IN " in sql and " o_id IN " in sql
    assert params == ["n1", "n2", "n1", "n2"]


def test_teardown_sql_failure_does_not_raise():
    from tests.tck.environment import _teardown_label

    ctx, cursor = _ctx()
    cursor.execute.side_effect = RuntimeError("boom")
    _teardown_label(ctx, "TCK_abc")  # must not raise
