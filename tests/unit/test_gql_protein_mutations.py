"""Unit tests for the GraphQL protein mutations' embedding SQL.

`Graph_KG.kg_NodeEmbeddings` is keyed `(graph_id, node_id)` and has no `id`
column.  A DDL-created table still carries an implicit RowID spelled `ID`, so the
old spellings did not error the way a typo would:

* `INSERT INTO kg_NodeEmbeddings (id, emb)` is `SQLCODE -108` — the RowID is not
  insertable, so `createProtein` with an embedding always failed;
* `DELETE FROM kg_NodeEmbeddings WHERE id = ?` compares an integer RowID with a
  node ID, deletes nothing, and then the `nodes` delete is refused by
  `fk_emb_node` — so `deleteProtein` failed for any protein that had a vector.

These tests read the SQL the resolvers issue, which is where both defects live.
"""

import asyncio
import types as pytypes

import pytest

from api.gql.resolvers.mutation import Mutation
from api.gql.types import CreateProteinInput


class _FakeCursor:
    def __init__(self, exists=0):
        self.sql = []
        self._exists = exists

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))

    def executemany(self, sql, seq):
        self.sql.append((" ".join(sql.split()), list(seq)))

    def fetchone(self):
        return (self._exists,)

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self._cursor

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _FakeLoader:
    def __init__(self, data=None):
        self._data = data
        self.cleared = []

    async def load(self, key):
        return self._data

    def clear(self, key):
        self.cleared.append(key)


def _info(conn, loader):
    return pytypes.SimpleNamespace(
        context={"db_connection": conn, "protein_loader": loader}
    )


def _run(coro):
    """Own the loop rather than borrowing the ambient one.

    `asyncio.get_event_loop()` raises `RuntimeError: There is no current event loop` once
    anything earlier in the session has closed the MainThread loop — which pytest-asyncio
    in AUTO mode does. These four tests therefore passed alone and failed in the full
    suite, reported as if the mutations were broken.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _emb_sql(cursor):
    return [s for s, _ in cursor.sql if "kg_NodeEmbeddings" in s]


class TestCreateProteinEmbedding:

    def test_insert_names_node_id_not_id(self):
        cursor = _FakeCursor(exists=0)
        conn = _FakeConn(cursor)
        loader = _FakeLoader(
            {"id": "PROTEIN:X", "labels": ["Protein"], "properties": {}, "name": "X"}
        )
        inp = CreateProteinInput(id="PROTEIN:X", name="X", embedding=[0.5] * 768)

        _run(Mutation().create_protein(_info(conn, loader), inp))

        (sql,) = _emb_sql(cursor)
        assert "(node_id, emb)" in sql
        assert "(id, emb)" not in sql

    def test_no_embedding_writes_no_embedding_row(self):
        cursor = _FakeCursor(exists=0)
        conn = _FakeConn(cursor)
        loader = _FakeLoader(
            {"id": "PROTEIN:X", "labels": ["Protein"], "properties": {}, "name": "X"}
        )

        _run(
            Mutation().create_protein(
                _info(conn, loader), CreateProteinInput(id="PROTEIN:X", name="X")
            )
        )

        assert _emb_sql(cursor) == []


class TestDeleteProteinEmbedding:

    def test_delete_matches_on_node_id(self):
        cursor = _FakeCursor(exists=1)
        conn = _FakeConn(cursor)
        loader = _FakeLoader()

        _run(Mutation().delete_protein(_info(conn, loader), "PROTEIN:X"))

        (sql,) = _emb_sql(cursor)
        assert "WHERE node_id = ?" in sql

    def test_embedding_delete_precedes_node_delete(self):
        """`fk_emb_node` points at `nodes`, so a left-behind vector refuses the node
        delete with `SQLCODE -121`."""
        cursor = _FakeCursor(exists=1)
        conn = _FakeConn(cursor)

        _run(Mutation().delete_protein(_info(conn, _FakeLoader()), "PROTEIN:X"))

        order = [s for s, _ in cursor.sql if s.startswith("DELETE")]
        assert "kg_NodeEmbeddings" in order[0]
        assert order[-1].startswith("DELETE FROM nodes")
