"""The temporal ``rdf_edges`` mirror reads both spellings of "no graph".

``create_edge_temporal(graph=...)`` and ``bulk_create_edges_temporal(graph=...)``
mirror each temporal edge into ``rdf_edges`` so the SQL readers can see it. The
mirror is written with a ``WHERE NOT EXISTS`` guard so a repeat write is a no-op.

That guard spelled the graph one way only: ``graph_id=?``. ``rdf_edges.graph_id``
is the one column that is still nullable (`_engine/schema.py:383`), so a row
written before the spec-214 backfill holds NULL. Asked for the default graph
(``graph=""``), the guard binds ``''``, `NULL = ''` is unknown rather than true,
the guard finds nothing, and the insert fires — producing a second row for the
same ``(s, p, o_id)``. ``u_spo_graph UNIQUE(s, p, o_id, graph_id)`` does not stop
it either, because NULL and ``''`` are distinct keys. So the default-graph mirror
duplicates every edge it already has, once per write, for as long as the NULL row
survives.

Both inserts sit inside ``except Exception: pass``, so nothing surfaces.

The idiom is ADR-0003's, as used at `_engine/nodes_edges.py:830` and
`_engine/snapshot.py:267`: COALESCE the *guard*, leave the written payload as the
literal the caller passed. A named graph is never NULL, so the COALESCE on the
bound side costs nothing and keeps one statement for both cases.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from iris_vector_graph._engine.temporal import TemporalMixin


class _Engine(TemporalMixin):
    """Just enough engine to reach the mirror: a store, a cursor, a table map.

    Deliberately does not inherit LedgerMixin — ``ledger_strict`` swallows the
    AttributeError and reports False, which is the non-strict path under test.
    """

    def __init__(self):
        self.cursor = MagicMock()
        self.conn = MagicMock()
        self.conn.cursor.return_value = self.cursor
        self._store = MagicMock()
        self._store.write_temporal_edge.return_value = MagicMock(error=None)
        self._store.bulk_write_temporal_edges.return_value = MagicMock(rows=[[1]])

    def _t(self, table: str) -> str:
        return f"Graph_KG.{table}"


def _edge_mirror_calls(engine) -> list[tuple[str, list]]:
    """Every statement the mirror sent to ``rdf_edges``, with its parameters."""
    return [
        (c.args[0], c.args[1])
        for c in engine.cursor.execute.call_args_list
        if "rdf_edges" in c.args[0]
    ]


def _single(engine, graph):
    eng = engine
    eng.create_edge_temporal("a", "R", "b", timestamp=100, graph=graph)
    return _edge_mirror_calls(eng)


def _bulk(engine, graph):
    eng = engine
    eng.bulk_create_edges_temporal(
        [{"s": "a", "p": "R", "o": "b", "ts": 100}], graph=graph
    )
    return _edge_mirror_calls(eng)


WRITERS = [(_single, "create_edge_temporal"), (_bulk, "bulk_create_edges_temporal")]


@pytest.mark.parametrize("writer,name", WRITERS, ids=[n for _, n in WRITERS])
def test_the_guard_reads_both_spellings_of_the_default_graph(writer, name):
    calls = writer(_Engine(), "")

    assert calls, f"{name} emitted no rdf_edges mirror, so this test proves nothing"
    for sql, _params in calls:
        guard = sql.split("WHERE NOT EXISTS")[1]
        assert "COALESCE(graph_id, '') = COALESCE(?, '')" in guard, (
            "the existence guard matches only the spelling the caller passed, so "
            f"a NULL graph_id row is invisible to it and the insert repeats: {sql}"
        )


@pytest.mark.parametrize("writer,name", WRITERS, ids=[n for _, n in WRITERS])
def test_the_written_row_still_carries_the_graph_verbatim(writer, name):
    """COALESCE belongs in the guard. The row written is the caller's value."""
    for sql, params in writer(_Engine(), ""):
        payload = sql.split("WHERE NOT EXISTS")[0]
        assert "COALESCE" not in payload, f"COALESCE leaked into the payload: {sql}"
        assert params[:4] == ["a", "R", "b", ""]


@pytest.mark.parametrize("writer,name", WRITERS, ids=[n for _, n in WRITERS])
def test_a_named_graph_binds_its_own_name(writer, name):
    """One statement serves both cases: COALESCE is a no-op on a named graph."""
    calls = writer(_Engine(), "acme")

    assert calls
    for sql, params in calls:
        assert params[:4] == ["a", "R", "b", "acme"]
        assert params[4:] == ["a", "R", "b", "acme"]


@pytest.mark.parametrize("writer,name", WRITERS, ids=[n for _, n in WRITERS])
def test_the_parameter_count_still_matches_the_placeholders(writer, name):
    """COALESCE(?, '') is still one placeholder. Miscount here binds silently wrong."""
    for sql, params in writer(_Engine(), ""):
        assert sql.count("?") == len(params), sql
