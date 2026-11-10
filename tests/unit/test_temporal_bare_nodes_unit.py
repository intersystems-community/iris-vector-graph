"""The Python surface of the bare-node retirement: argument shapes and result parsing, no IRIS needed.

The behaviour is in ``tests/e2e/test_temporal_bare_nodes_e2e.py``. These pin what a stubbed store call must look like, so
that a caller on an older deployed class (no ``retire_bare_nodes`` argument) is not sent one it cannot take.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from iris_vector_graph._engine.temporal import BareNodesResult, PurgeResult
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore


def _store(returns):
    store = IRISGraphStore(MagicMock())
    calls = []

    def fake(cls, method, *args):
        calls.append((cls, method, args))
        return returns

    store._call_classmethod = fake
    store._has_eraser_method = lambda m: True
    return store, calls


def test_purge_result_defaults_to_no_nodes_retired_and_still_converts_to_int():
    r = PurgeResult(7, 2)
    assert r.nodes_retired == 0 and int(r) == 7


@pytest.mark.parametrize(
    "raw,expected",
    [("5", (5, 0, 0)), ("5:2", (5, 2, 0)), ("5:2:9", (5, 2, 9)), ("0:0:0", (0, 0, 0))],
)
def test_purge_raw_before_parses_one_two_or_three_fields(raw, expected):
    store, _ = _store(raw)
    assert tuple(store.purge_raw_before(100, retire_bare_nodes=raw.count(":") == 2)) == expected


def test_the_option_is_sent_unless_turned_off():
    """4.7.0 row 6: on by default; turned off, the call keeps the three arguments a class older than it takes."""
    store, calls = _store("1:0:1")
    store.purge_raw_before(100, graph="g")
    assert calls[-1][1] == "PurgeRawBefore" and len(calls[-1][2]) == 4 and calls[-1][2][3] == 1
    store, calls = _store("1:0")
    store.purge_raw_before(100, graph="g", retire_bare_nodes=False)
    assert len(calls[-1][2]) == 3


def test_erase_bare_nodes_sends_the_arguments_in_the_order_the_class_takes_them_and_parses_the_counts():
    payload = {"graph": "g", "bare": 30, "erased": 0, "kept": 0, "batches": 0, "dry_run": 1}
    store, calls = _store(json.dumps(payload))
    r = store.erase_bare_nodes(graph="g", batch_size=500, dry_run=True, min_age_seconds=60)
    assert calls[-1] == ("Graph.KG.Eraser", "EraseBareNodes", ("g", 500, 1, 60))
    assert r == BareNodesResult(bare=30, erased=0, kept=0, batches=0, dry_run=True)


def test_erase_bare_nodes_refuses_when_the_class_is_not_deployed():
    store, _ = _store("{}")
    store._has_eraser_method = lambda m: False
    with pytest.raises(RuntimeError, match="EraseBareNodes is not deployed"):
        store.erase_bare_nodes()


def test_erase_bare_nodes_rejects_a_graph_name_that_cannot_be_a_subscript():
    store, _ = _store("{}")
    with pytest.raises(ValueError):
        store.erase_bare_nodes(graph="bad\x01name")
