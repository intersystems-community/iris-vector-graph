"""4.6.0 R252: ``bulk_create_edges_temporal`` writes a given weight of 0 as 0.

``float(e.get("w") or e.get("weight", 1.0))`` read a ``w`` of 0 (or 0.0) as absent, so the edge was stored with
the default weight 1.0 (fixes26). A given weight is now written as given, 0 included; an edge without one still gets
1.0, and ``None`` still means "not given". This is a new write of the given value: an edge already stored keeps the
1.0 it was written with. The live half is tests/e2e/test_460_r252_temporal_zero_weight_e2e.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult


def _engine():
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng._schema_prefix = "Graph_KG"
    store = MagicMock()
    store.bulk_write_temporal_edges.return_value = IVGResult(columns=["n"], rows=[[1, 0, 0]])
    eng._store = store
    eng.conn = MagicMock()
    return eng, store


@pytest.mark.parametrize(
    "extra, weight",
    [
        ({"w": 0}, 0.0),
        ({"w": 0.0}, 0.0),
        ({"weight": 0}, 0.0),
        ({"w": 2.5}, 2.5),
        ({"weight": 3}, 3.0),
        ({"w": -1}, -1.0),
        ({}, 1.0),
        ({"w": None}, 1.0),
        ({"w": 0, "weight": 7}, 0.0),  # `w` first, as before
    ],
)
def test_the_given_weight_is_written(extra, weight):
    eng, store = _engine()
    eng.bulk_create_edges_temporal([{"s": "a", "p": "P", "o": "b", "ts": 100, **extra}])
    (written,) = store.bulk_write_temporal_edges.call_args.args[0]
    assert written["weight"] == weight and isinstance(written["weight"], float)
