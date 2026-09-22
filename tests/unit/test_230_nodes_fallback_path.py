"""`nodes` must return the rows it found when the context has no `node_loader`.

`CoreQuery.nodes` batch-loads through `info.context["node_loader"]` and falls back to
loading one node at a time when the key is absent. Neither app factory puts a
`node_loader` in the context, so the fallback is the only path that ever runs — and it
called `await self.node(...)`, where `self` is the Strawberry root value, which is None
for a query root. Every `nodes(labels: [...])` that matched at least one row therefore
died with

    AttributeError: 'NoneType' object has no attribute 'node'

Until the context started carrying `engine`, the resolver returned `[]` before reaching
this line, so the crash was invisible. See
`tests/unit/test_230_graphql_context_carries_engine.py`.

The schema is executed here rather than the resolver being called directly, because
`self is None` is exactly the thing under test and only Strawberry produces it.
"""

import pytest

from api.gql.schema import schema


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)


class _FakeEngine:
    """Answers the two calls `nodes` + `node` make: a cursor and `get_node`."""

    def __init__(self, node_ids):
        self.conn = _FakeConn([(n,) for n in node_ids])
        self._ids = node_ids
        self.get_node_calls = []
        self.graphs_asked_for = []

    def get_node(self, node_id, graph=None):
        self.get_node_calls.append(node_id)
        self.graphs_asked_for.append(graph)
        if node_id not in self._ids:
            return None
        return {"labels": ["Widget"], "properties": {"name": node_id}, "created_at": None}


async def _run(query, engine):
    # The resolvers are async, so `execute_sync` cannot run them at all.
    return await schema.execute(query, context_value={"engine": engine})


async def test_nodes_returns_the_rows_without_a_node_loader():
    engine = _FakeEngine(["w:1", "w:2"])
    result = await _run('{ nodes(labels: ["Widget"], limit: 10) { id labels } }', engine)
    assert result.errors is None, result.errors
    assert [n["id"] for n in result.data["nodes"]] == ["w:1", "w:2"]
    assert result.data["nodes"][0]["labels"] == ["Widget"]
    assert engine.get_node_calls == ["w:1", "w:2"], (
        "the fallback path loads each id it selected"
    )


async def test_nodes_skips_an_id_whose_node_has_since_gone():
    # `get_node` answering None for a selected id must drop the row, not the query.
    engine = _FakeEngine(["w:1"])
    engine.conn = _FakeConn([("w:1",), ("w:gone",)])
    result = await _run('{ nodes(labels: ["Widget"], limit: 10) { id } }', engine)
    assert result.errors is None, result.errors
    assert [n["id"] for n in result.data["nodes"]] == ["w:1"]


async def test_nodes_without_an_engine_is_empty_not_an_error():
    result = await schema.execute(
        '{ nodes(labels: ["Widget"], limit: 10) { id } }', context_value={"engine": None}
    )
    assert result.errors is None, result.errors
    assert result.data["nodes"] == []


async def test_a_stray_node_loader_in_the_context_is_ignored():
    """The batch branch is gone, and nothing may resurrect it.

    It keyed on `info.context["node_loader"]`, a loader class that does not exist in
    `api/gql/loaders.py` and that no factory supplied — and it read no graph, so a context
    carrying one would have gone back to merging every graph's rows.
    """

    class _Exploding:
        async def load_many(self, ids):  # pragma: no cover - must never be called
            raise AssertionError("the removed batch path ran")

    engine = _FakeEngine(["w:1"])
    result = await schema.execute(
        '{ nodes(labels: ["Widget"], limit: 10) { id } }',
        context_value={"engine": engine, "node_loader": _Exploding()},
    )
    assert result.errors is None, result.errors
    assert [n["id"] for n in result.data["nodes"]] == ["w:1"]
    assert engine.graphs_asked_for == [""], engine.graphs_asked_for


@pytest.mark.parametrize("node_id", ["w:1", "urn:widget:7"])
async def test_node_by_id_still_works(node_id):
    engine = _FakeEngine([node_id])
    result = await _run('{ node(id: "%s") { id labels } }' % node_id, engine)
    assert result.errors is None, result.errors
    assert result.data["node"]["id"] == node_id
