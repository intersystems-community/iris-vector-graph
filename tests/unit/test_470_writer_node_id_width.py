"""4.7.0 (integration triage): every writer the CHANGELOG names refuses a node id wider than `nodes.node_id` before
it sends anything.

CHANGELOG 4.7.0: "`create_node`, `bulk_create_nodes`, `bulk_create_edges` and `BulkLoader` refuse a too-wide label,
property key, predicate or node id". create_node checked its labels and keys and not the id: a 500-character id
reached IRIS, which failed with SQLCODE -400 <SUBSCRIPT> (GraphWriteError) on the id's own `id` property row (R089)
(tests/integration/test_diabolical_qa.py). The id width is NODE_ID_WIDTH, 256 characters (`nodes.node_id`).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from iris_vector_graph._widths import NODE_ID_WIDTH
from iris_vector_graph.engine import IRISGraphEngine

WIDE = "x" * (NODE_ID_WIDTH + 1)
FITS = "y" * NODE_ID_WIDTH


def _engine():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.return_value = (0,)
    cursor.fetchall.return_value = []
    cursor.description = []
    cursor.rowcount = 1
    engine = IRISGraphEngine(conn, embedding_dimension=4)
    cursor.execute.reset_mock()
    cursor.executemany.reset_mock()
    return engine, cursor


def _loader(engine):
    from iris_vector_graph.bulk_loader import BulkLoader

    return BulkLoader(engine.conn)


def _writes(cursor):
    calls = cursor.execute.call_args_list + cursor.executemany.call_args_list
    return [str(c.args[0]) for c in calls if c.args and str(c.args[0]).lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]


@pytest.mark.parametrize("call", [
    lambda e: e.create_node(WIDE, labels=["L"]),
    lambda e: e.bulk_create_nodes([{"id": "ok", "labels": ["L"]}, {"id": WIDE}]),
    lambda e: e.bulk_create_edges([{"source_id": WIDE, "predicate": "R", "target_id": "b"}]),
    lambda e: e.bulk_create_edges([{"source_id": "a", "predicate": "R", "target_id": WIDE}]),
    lambda e: _loader(e).load_nodes([("ok", {}), (WIDE, {})]),
    lambda e: _loader(e).load_edges([("a", "R", WIDE, {})]),
], ids=["create_node", "bulk_create_nodes", "bulk_create_edges source", "bulk_create_edges target",
        "BulkLoader.load_nodes", "BulkLoader.load_edges"])
def test_a_too_wide_node_id_is_refused_before_anything_is_sent(call):
    engine, cursor = _engine()
    with pytest.raises(ValueError, match=rf"is {NODE_ID_WIDTH + 1} characters; the column holds {NODE_ID_WIDTH}"):
        call(engine)
    assert _writes(cursor) == []


def test_an_id_of_exactly_the_width_is_written():
    engine, cursor = _engine()
    engine.create_node(FITS, labels=["L"])
    assert any("INSERT" in w and "nodes" in w for w in _writes(cursor))
