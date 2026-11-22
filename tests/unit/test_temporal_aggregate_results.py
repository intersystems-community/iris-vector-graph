"""Empty and nonempty temporal aggregates through the engine and SQL store."""

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore


@pytest.mark.parametrize("server_answer", [None, ""])
def test_store_reads_an_empty_aggregate_as_zero(server_answer):
    store = IRISGraphStore.__new__(IRISGraphStore)
    store._call_classmethod = MagicMock(return_value=server_answer)
    assert store.get_temporal_aggregate("s", "T", "min", 200, 200).rows == [[0.0]]


def test_store_still_reads_a_value():
    store = IRISGraphStore.__new__(IRISGraphStore)
    store._call_classmethod = MagicMock(return_value="2.5")
    assert store.get_temporal_aggregate("s", "T", "max", 0, 9).rows == [[2.5]]


@pytest.mark.parametrize("metric,expected", [("min", 0.0), ("avg", 0.0), ("count", 0)])
def test_engine_answers_zero_when_no_row_comes_back(metric, expected):
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng._store = MagicMock()
    eng._store.get_temporal_aggregate.return_value = IVGResult(columns=["value"], rows=[])
    got = eng.get_temporal_aggregate("s", "T", metric, 0, 9)
    assert got == expected and type(got) is type(expected)
