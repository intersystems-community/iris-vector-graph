"""A routed table outlives its registry row, and then nothing can be erased.

`_create_route` creates the table, attempts the index, and inserts the registry row
— in that order, and without committing. In IRIS, `CREATE TABLE` is DDL and is
durable the moment it runs; the registry row is not. So any rollback after the
create — the caller's own, or the race handler a few lines below, or a test fixture's
— leaves the table standing with no row naming it.

That orphan is not cosmetic. A routed table carries a foreign key on
`(graph_id, node_id)`, and both `Graph.KG.Eraser` erase paths collect the routed
tables they must empty *from the registry*. An orphan is therefore never emptied, so
`DELETE FROM Graph_KG.nodes` fails its referential check with `SQLCODE -124` and the
whole erase rolls back: `erase failed on nodes with SQLCODE -124; nothing was
erased`. One orphan table breaks every erase in the namespace, including the fixture
teardowns, which is how a single stray table turned into 311 setup errors in one
integration chunk.

These tests pin the Python half: the registry row must be committed as soon as it is
written, so it is as durable as the table it names. The Eraser half — discovering
`kg_emb_` tables from the catalog as well as the registry — is pinned live in
`tests/integration/test_227_orphan_route_erase.py`, because it is a statement about
what IRIS's foreign keys do.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.routing import route_table_name


def _engine(dim: int = 4):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = (0,)
    cursor.description = [("node_id", None)]
    engine = IRISGraphEngine(conn, embedding_dimension=dim)
    conn.commit.reset_mock()
    return engine, conn, cursor


class TestCreateRouteDurability:
    def test_the_registry_row_is_committed_before_the_call_returns(self, monkeypatch):
        """Otherwise the table is durable and the row naming it is not."""
        engine, conn, _cursor = _engine()

        monkeypatch.setattr(type(engine), "_create_routed_table", lambda *a, **k: None)
        monkeypatch.setattr(
            type(engine), "_attempt_route_index", lambda *a, **k: ("absent", None)
        )
        monkeypatch.setattr(type(engine), "_insert_identity", lambda *a, **k: None)
        monkeypatch.setattr(type(engine), "_record_route_index_state", lambda *a, **k: None)

        route = engine._create_route("g", "m", dimension=4, dtype="DOUBLE")

        assert route is not None
        assert route.table_name == route_table_name("g", "m")
        assert conn.commit.call_count >= 1, (
            "the routed table is already durable (DDL); an uncommitted registry row "
            "means a rollback leaves an orphan that blocks every erase"
        )

    def test_a_failed_insert_does_not_commit(self, monkeypatch):
        """The race handler rolls back; it must not have committed a half-written row."""
        engine, conn, _cursor = _engine()

        monkeypatch.setattr(type(engine), "_create_routed_table", lambda *a, **k: None)
        monkeypatch.setattr(
            type(engine), "_attempt_route_index", lambda *a, **k: ("absent", None)
        )

        def _boom(*_a, **_kw):
            raise RuntimeError("primary key refused the second row")

        monkeypatch.setattr(type(engine), "_insert_identity", _boom)
        monkeypatch.setattr(type(engine), "_read_route", lambda *a, **k: (True, None))

        with pytest.raises(RuntimeError):
            engine._create_route("g", "m", dimension=4, dtype="DOUBLE")

        assert conn.commit.call_count == 0
        assert conn.rollback.call_count >= 1
