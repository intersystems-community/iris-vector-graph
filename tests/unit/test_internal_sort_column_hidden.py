"""ORDER BY with SKIP / LIMIT projects its sort keys as __sortN columns; they are the
translator's, not the query's, and never reach the caller (ReturnSkipLimit1-3)."""
from types import SimpleNamespace

from iris_vector_graph._engine.query import _drop_internal_columns


def _result(columns, rows, bolt=None):
    return SimpleNamespace(columns=columns, rows=rows, bolt_column_types=bolt or [])


def test_sort_columns_removed_with_their_values():
    r = _result(["n", "__sort0", "m", "__sort1_n"], [[1, "a", 2, 3], [4, "b", 5, 6]])
    _drop_internal_columns(r)
    assert r.columns == ["n", "m"]
    assert r.rows == [[1, 2], [4, 5]]


def test_bolt_types_follow_the_columns():
    r = _result(["n", "__sort0"], [[1, 2]], bolt=["node", "scalar"])
    _drop_internal_columns(r)
    assert r.bolt_column_types == ["node"]


def test_bolt_types_already_at_return_arity_left_alone():
    r = _result(["n", "__sort0"], [[1, 2]], bolt=["node"])
    _drop_internal_columns(r)
    assert r.bolt_column_types == ["node"]


def test_no_internal_columns_is_a_no_op():
    rows = [(1, 2)]
    r = _result(["a", "sort0"], rows)
    _drop_internal_columns(r)
    assert r.columns == ["a", "sort0"] and r.rows is rows
