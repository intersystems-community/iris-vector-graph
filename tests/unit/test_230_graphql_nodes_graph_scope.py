"""`CoreQuery.nodes` selects from one graph, with a schema-qualified order-by join.

Two defects in the same SQL builder (`api/gql/core/resolvers.py`):

1. No graph predicate anywhere. `FROM Graph_KG.nodes n JOIN Graph_KG.rdf_labels l ON
   l.s = n.node_id` joins a label row in graph B to a node in graph A, and the result set
   spans every graph in the namespace — the same scope leak spec 227 closed on the
   embedding tables, still open on the published GraphQL API.

2. The order-by-a-property path emitted `LEFT JOIN rdf_props order_p` with no schema
   prefix. Unqualified names resolve against the connection's default schema (`SQLUser`),
   so `nodes(orderBy: "name")` — a documented argument — failed at Query Open instead of
   ordering.

The SQL is inspected through a fake cursor rather than executed: what is under test is the
text the builder emits and the parameters it binds alongside it.
"""

import pytest

from api.gql.schema import schema
from iris_vector_graph.constants import DEFAULT_GRAPH


class _RecordingCursor:
    def __init__(self, sink):
        self._sink = sink

    def execute(self, sql, params=None):
        self._sink.append((sql, list(params or [])))

    def fetchall(self):
        return []

    def close(self):
        pass


class _RecordingEngine:
    """Records the SQL `nodes` builds; returns no rows, so nothing else is exercised."""

    def __init__(self):
        self.statements = []
        self.conn = self

    def cursor(self):
        return _RecordingCursor(self.statements)


async def _nodes_sql(query):
    engine = _RecordingEngine()
    result = await schema.execute(query, context_value={"engine": engine})
    assert result.errors is None, result.errors
    assert engine.statements, "the resolver built no SQL"
    return engine.statements[0]


@pytest.mark.parametrize(
    "query",
    [
        '{ nodes(labels: ["Widget"]) { id } }',
        '{ nodes(where: {key: "k", value: "v"}) { id } }',
        '{ nodes(labels: ["Widget"], where: {key: "k", value: "v"}) { id } }',
        "{ nodes { id } }",
    ],
)
async def test_every_shape_carries_a_graph_predicate(query):
    sql, params = await _nodes_sql(query)
    assert "n.graph_id = ?" in sql, sql
    assert DEFAULT_GRAPH in params, (sql, params)


async def test_the_label_join_cannot_cross_graphs():
    sql, _ = await _nodes_sql('{ nodes(labels: ["Widget"]) { id } }')
    assert "l.graph_id = n.graph_id" in sql, sql


async def test_the_property_filter_join_cannot_cross_graphs():
    sql, _ = await _nodes_sql('{ nodes(where: {key: "k", value: "v"}) { id } }')
    assert "p.graph_id = n.graph_id" in sql, sql


async def test_a_named_graph_is_used_instead_of_the_default():
    sql, params = await _nodes_sql('{ nodes(labels: ["Widget"], graph: "ga") { id } }')
    assert "n.graph_id = ?" in sql, sql
    assert "ga" in params, params


async def test_order_by_property_join_is_schema_qualified():
    sql, params = await _nodes_sql('{ nodes(orderBy: "name") { id } }')
    assert "Graph_KG.rdf_props order_p" in sql, sql
    assert "LEFT JOIN rdf_props" not in sql, sql
    assert "order_p.graph_id = n.graph_id" in sql, sql
    assert params[0] == "name", params
