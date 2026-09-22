"""Spec 230 — `arno_call` checks its own arity before IRIS does.

Every entry in `_SQL_FN_DISPATCH` carries the wrapper's fixed marker list, because each
`$ZF(-5)` wrapper is a distinct SQL function with a fixed signature. Nothing compared the
caller's `*args` against it, so a call short of an argument built
`SELECT ivg_arno_zf_call_kg_triangle(?, ?, ?)` with one parameter and came back as
`<ARGUMENT ERROR> Bad argument given; Details: Incorrect number of parameters` — wrapped
in an `ArnoError` that blames the kernel function for the caller's mistake and names
neither the expected count nor the given one.
"""

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.stores import arno_bridge
from iris_vector_graph.stores.arno_bridge import ArnoError, arno_call, clear_probe_cache


@pytest.fixture(autouse=True)
def _available():
    """A loaded library, so the arity check is what the call reaches."""
    clear_probe_cache()
    conn = MagicMock()
    arno_bridge._probe_cache[arno_bridge._conn_key(conn)] = {
        "available": True,
        "lib_path": "/fake/libarno_callout.so",
    }
    yield conn
    clear_probe_cache()


def test_too_few_arguments_names_both_counts(_available):
    with pytest.raises(ArnoError) as exc:
        arno_call(_available, "kg_triangle_count_global")

    message = str(exc.value)
    assert "kg_triangle_count_global" in message
    assert "2" in message and "0" in message
    # Refused before the statement was built, so IRIS was never asked.
    assert _available.cursor.return_value.execute.call_count == 0


def test_too_many_arguments_is_refused_too(_available):
    with pytest.raises(ArnoError, match="expects 2"):
        arno_call(_available, "kg_scc_global", "^KG", 10, "extra")


def test_the_right_arity_still_reaches_iris(_available):
    cursor = _available.cursor.return_value
    cursor.fetchone.return_value = ('{"triangles": 3}',)

    assert arno_call(_available, "kg_triangle_count_global", "^KG", 10) == '{"triangles": 3}'
    assert cursor.execute.call_count == 1


def test_a_seven_marker_wrapper_counts_seven(_available):
    """`kg_leiden_global` takes seven, which is why the count is read per entry."""
    with pytest.raises(ArnoError, match="expects 7"):
        arno_call(_available, "kg_leiden_global", "^KG", 10)
