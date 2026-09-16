"""PROV-O export passes a graph key to QueryWindow (spec 223).

Spec 223 made every `Graph.KG.TemporalIndex` method take `graphId` as its *first*
parameter. `_get_temporal_edges_window` was missed, so its four positional
arguments landed one slot to the left:

    graphId  <- ""
    source   <- ""
    predicate <- ts_start
    tsStart  <- ts_end
    tsEnd    <- (nothing)

which fails inside IRIS with `<UNDEFINED> *tsEnd`. The whole body sits in a bare
`except Exception` that logs at warning and returns `[]`, so every PROV-O export
has been silently producing an empty graph rather than raising.

Every other test in tests/unit/test_prov_export.py mocks
`_get_temporal_edges_window` itself, which is exactly why nothing caught this.
These tests assert the call shape one level lower, at the classmethod bridge.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from iris_vector_graph._engine.prov import ProvMixin


class _Engine(ProvMixin):
    """The narrowest thing that can make the call: just the IRIS bridge."""

    def __init__(self, payload="[]"):
        self.iris_obj = MagicMock()
        self.iris_obj.classMethodValue.return_value = payload

    def _iris_obj(self):
        return self.iris_obj


def _call_args(engine):
    return engine.iris_obj.classMethodValue.call_args[0]


def test_the_call_carries_the_spec_223_graph_key():
    """Method name, then graphId, then the four pre-223 arguments."""
    eng = _Engine()

    eng._get_temporal_edges_window(100, 200)

    args = _call_args(eng)
    assert args[0] == "Graph.KG.TemporalIndex"
    assert args[1] == "QueryWindow"
    assert args[2] == "", "graphId is missing, so every later argument is shifted"


def test_the_window_bounds_reach_the_window_parameters():
    """tsStart/tsEnd are the last two arguments, not predicate/tsStart."""
    eng = _Engine()

    eng._get_temporal_edges_window(100, 200)

    graph_id, source, predicate, ts_start, ts_end = _call_args(eng)[2:]
    assert (graph_id, source, predicate) == ("", "", "")
    assert (ts_start, ts_end) == (100, 200)


def test_an_open_window_still_sends_both_bounds():
    """`None` means "no bound", and IRIS has no default for tsEnd."""
    eng = _Engine()

    eng._get_temporal_edges_window(None, None)

    ts_start, ts_end = _call_args(eng)[-2:]
    assert ts_start == 0
    assert ts_end == 9999999999


def test_the_arity_matches_the_classmethod_signature():
    """QueryWindow(graphId, source, predicate, tsStart, tsEnd) — five arguments."""
    eng = _Engine()

    eng._get_temporal_edges_window(1, 2)

    assert len(_call_args(eng)) == 7, (
        "expected class name + method name + 5 arguments"
    )


def test_the_rows_still_come_back_mapped():
    """The shift meant the mapping below never ran. Prove it does now."""
    eng = _Engine(
        json.dumps([{"s": "a", "p": "R", "o": "b", "ts": 100, "ts_end": 200}])
    )

    edges = eng._get_temporal_edges_window(0, 9999999999)

    assert edges == [
        {
            "edge_id": "a|R|b|100",
            "source": "a",
            "predicate": "R",
            "target": "b",
            "ts_start": 100,
            "ts_end": 200,
        }
    ]
