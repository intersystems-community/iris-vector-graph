"""4.6.0 R050 W5: k_core / triangle_count and their procedures break ties by node ID in IRIS collation.

By hand: IRIS collation puts a canonical integer first, by value, then the rest as text: 9, 10, a, b. Python string
order would put 10 before 9. The live answers, on every route, are in tests/e2e/test_460_r050_kcore_triangles_ties_e2e.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.stores.iris_sql_store import _ranked_rows

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("top_k,expected", [(0, ["x", "9", "10", "a", "b", "z"]), (3, ["x", "9", "10"])])
def test_ties_go_in_iris_collation(top_k, expected):
    rows = [["b", 2], ["10", 2], ["z", 1], ["a", 2], ["9", 2], ["x", 3]]
    assert [r[0] for r in _ranked_rows(rows, top_k)] == expected


def test_rows_keep_their_other_columns():
    assert _ranked_rows([["b", 1, 0.5], ["a", 1, 1.0]], 0) == [["a", 1, 1.0], ["b", 1, 0.5]]


@pytest.mark.parametrize("method", ["KCoreJson", "TriangleCountJson"])
def test_the_procedures_order_by_node_id(method):
    src = (ROOT / "iris_src/src/Graph/KG/Communities.cls").read_text()
    body = src.split(f"ClassMethod {method}(", 1)[1].split("\n}\n", 1)[0]
    # the sort global's second subscript is the node ID, read from ^NKG("$ND"), not the interned index
    assert '$Get(^NKG("$ND", idx), idx)) =' in body


@pytest.mark.parametrize("call,run", [("_k_core_arno", "kg_kcore_run"), ("_triangle_count_arno", "kg_triangle_count_run")])
def test_the_library_is_asked_for_every_node(call, run):
    from iris_vector_graph.stores.iris_sql_store import IRISGraphStore

    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = MagicMock()
    with patch("iris_vector_graph.stores.arno_bridge.build_kg_adjacency_chunked", return_value=({}, 0)), patch(
        "iris_vector_graph.stores.arno_bridge.arno_call", return_value="[]"
    ) as arno:
        getattr(store, call)(2)
    assert arno.call_args.args[1:3] == (run, 0)
