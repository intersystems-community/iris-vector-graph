"""4.6.0 R310 follow-up, live (fixes66): an UNWIND of a parameter's list, or of a WITH bound to a list literal, answers
its booleans and '' as themselves under either driver; they answered '1', '0' and null. The expected rows are the
lists themselves (tests/unit/test_460_r310_unwind_known_booleans.py says why).
"""

from __future__ import annotations

import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]


@pytest.fixture()
def eng(iris_connection):
    return IRISGraphEngine(iris_connection)


def _col(e, q, p=None):
    r = e.execute_cypher(q, p or {}, raise_on_error=True)
    assert not r.error, r.error
    return [(type(row[0]).__name__, row[0]) for row in r.rows]


def _typed(vals):
    return [(type(v).__name__, v) for v in vals]


@pytest.mark.parametrize("vals", [[True, False, ""], [True, "", 1, "a", 2.5], [False]])
def test_a_parameter_list(eng, vals):
    assert _col(eng, "UNWIND $l AS b RETURN b", {"l": vals}) == _typed(vals)


@pytest.mark.parametrize("vals", [[True, False, ""], [True, "", 1, "a", 2.5]])
def test_a_with_of_a_parameter_list(eng, vals):
    assert _col(eng, "WITH $l AS l UNWIND l AS b RETURN b", {"l": vals}) == _typed(vals)


def test_a_with_bound_list_literal(eng):
    assert _col(eng, "WITH [true, false, '', 7] AS l UNWIND l AS b RETURN b") == _typed([True, False, "", 7])
