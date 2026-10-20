"""Spec 253, T011 (FR-006, FR-013): the shadow comparator that holds the native executor to the SQL path.

Exact where the query determines the answer: same columns; rows in order where the query orders them completely,
as a multiset otherwise. Where a LIMIT or SKIP keeps rows the query does not order completely, both paths may keep
different rows legitimately: then the row counts match and every native row is a row of the SQL result without the
LIMIT/SKIP. A difference names the query, the parameters and the first differing row.
"""

import pytest

from iris_vector_graph.cypher.native_shadow import NativeMismatch, compare


def test_equal_unordered_results_in_any_order():
    compare("q", {}, ["a"], [[1], [2]], ["a"], [[2], [1]], ordered=False)


def test_a_missing_row_is_a_mismatch_naming_it():
    with pytest.raises(NativeMismatch) as e:
        compare("MATCH ...", {"x": 1}, ["a"], [[1], [2]], ["a"], [[1]], ordered=False)
    msg = str(e.value)
    assert "MATCH ..." in msg and "'x': 1" in msg and "[2]" in msg


def test_duplicates_count():
    with pytest.raises(NativeMismatch):
        compare("q", {}, ["a"], [[1], [1]], ["a"], [[1]], ordered=False)


def test_ordered_results_compare_in_order():
    compare("q", {}, ["a"], [[1], [2]], ["a"], [[1], [2]], ordered=True)
    with pytest.raises(NativeMismatch):
        compare("q", {}, ["a"], [[1], [2]], ["a"], [[2], [1]], ordered=True)


def test_columns_must_match():
    with pytest.raises(NativeMismatch, match="columns"):
        compare("q", {}, ["a"], [[1]], ["b"], [[1]], ordered=False)


def test_value_types_count():
    with pytest.raises(NativeMismatch):
        compare("q", {}, ["a"], [[1]], ["a"], [["1"]], ordered=False)


def test_a_limit_over_a_partial_order_accepts_any_rows_of_the_full_result():
    full = [[1], [2], [3], [4]]
    compare("q", {}, ["a"], [[1], [2]], ["a"], [[3], [4]], ordered=False, limited=True, sql_unlimited_rows=full)


def test_a_limit_over_a_partial_order_still_needs_the_same_count():
    with pytest.raises(NativeMismatch, match="count"):
        compare(
            "q", {}, ["a"], [[1], [2]], ["a"], [[3]], ordered=False, limited=True, sql_unlimited_rows=[[1], [2], [3]]
        )


def test_a_limit_over_a_partial_order_rejects_a_row_the_query_cannot_return():
    with pytest.raises(NativeMismatch):
        compare("q", {}, ["a"], [[1]], ["a"], [[9]], ordered=False, limited=True, sql_unlimited_rows=[[1], [2]])
