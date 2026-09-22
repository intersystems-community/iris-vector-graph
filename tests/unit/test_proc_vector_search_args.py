"""`CALL ivg.vector.search(...)` must hand the store real numbers.

The parser represents a Cypher list literal as a `Literal` whose `.value` is a
list of `Literal` nodes — the elements are *not* unwrapped. `_proc_ivg_vector_search`
passed that list straight through, and `execute_knn_vec` formats it with
`",".join(str(x))`, so the SQL parameter read
`[Literal(value=1.0),Literal(value=0.0),…]`. IRIS accepted it, scored nothing, and
the search reported zero neighbours with no error — every `CALL ivg.vector.search`
in every deployment.
"""

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.cypher.ast import Literal, Variable
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.result import IVGResult
from iris_vector_graph.stores.iris_sql_store import IRISGraphStore


class _Proc:
    def __init__(self, arguments, yield_items=None):
        self.arguments = arguments
        self.yield_items = yield_items or []


def _engine():
    eng = object.__new__(IRISGraphEngine)
    eng._store = MagicMock()
    eng._store.execute_knn_vec.return_value = IVGResult(columns=["id", "score"], rows=[])
    return eng


def test_nested_literal_elements_are_unwrapped():
    eng = _engine()
    vec = Literal(value=[Literal(value=1.0), Literal(value=0.0), Literal(value=0.5)])
    proc = _Proc([Literal(value="Gene"), Literal(value="embedding"), vec, Literal(value=3)])
    eng._proc_ivg_vector_search(proc)
    qv = eng._store.execute_knn_vec.call_args[0][0]
    assert qv == [1.0, 0.0, 0.5]
    assert all(isinstance(x, float) for x in qv)


def test_plain_float_elements_still_pass_through():
    eng = _engine()
    proc = _Proc(
        [Literal(value="Gene"), Literal(value="embedding"), Literal(value=[1.0, 2.0]), Literal(value=2)]
    )
    eng._proc_ivg_vector_search(proc)
    assert eng._store.execute_knn_vec.call_args[0][0] == [1.0, 2.0]


def test_integer_elements_are_accepted_as_numbers():
    eng = _engine()
    vec = Literal(value=[Literal(value=1), Literal(value=0)])
    proc = _Proc([Literal(value="Gene"), Literal(value="embedding"), vec, Literal(value=2)])
    eng._proc_ivg_vector_search(proc)
    assert eng._store.execute_knn_vec.call_args[0][0] == [1.0, 0.0]


def test_columns_are_the_yield_names():
    """The caller asked for `YIELD node, score`; `id` is not a column it named.

    The store answers `["id", "score"]`, which is right for the store's own
    interface and wrong for a Cypher result: `result["columns"].index("node")`
    raised `ValueError` for anyone reading the CALL's output by name.
    """
    eng = _engine()
    proc = _Proc([Literal(value="Gene"), Literal(value="embedding"), Literal(value=[1.0]), Literal(value=1)])
    proc.yield_items = ["node", "score"]
    result = eng._proc_ivg_vector_search(proc)
    assert result.columns == ["node", "score"]


def test_a_parameterised_query_vector_is_resolved():
    """`CALL ivg.vector.search('Gene', 'emb', $vec, 5)` is the documented shape.

    `$vec` is a `Variable`, not a `Literal`, so the handler fell through to
    `query_vector or []` and searched with an empty vector — zero rows, no error.
    """
    eng = _engine()
    proc = _Proc([Literal(value="Gene"), Literal(value="emb"), Variable(name="vec"), Literal(value=5)])
    eng._proc_ivg_vector_search(proc, parameters={"vec": [0.1, 0.2]})
    assert eng._store.execute_knn_vec.call_args[0][0] == [0.1, 0.2]


def test_use_graph_reaches_the_store():
    """`USE GRAPH 'A' CALL ivg.vector.search(...)` must be scoped to A.

    The graph lives on the parsed query, not on the procedure call, and this
    handler runs *before* the translator that spec 227 scoped — so the CALL was
    answered by an unscoped store search. SC-004's leak by a different route.
    """
    eng = _engine()
    proc = _Proc([Literal(value="Gene"), Literal(value="emb"), Literal(value=[1.0]), Literal(value=5)])
    eng._proc_ivg_vector_search(proc, graph="graphA")
    assert eng._store.execute_knn_vec.call_args.kwargs.get("graph") == "graphA"


@pytest.mark.parametrize("query_input", [Literal(value="flu symptoms"), Literal(value="seed:1")])
def test_a_non_vector_query_input_falls_through_to_sql(query_input):
    """Text and seed-node inputs are translated, not handled here.

    This handler only knows how to send a numeric vector. Answering a text or
    seed-node search with an empty vector reported "no neighbours" for a query
    the SQL path can actually run, so hand it back by returning None.
    """
    eng = _engine()
    proc = _Proc([Literal(value="Gene"), Literal(value="emb"), query_input, Literal(value=5)])
    assert eng._proc_ivg_vector_search(proc) is None
    eng._store.execute_knn_vec.assert_not_called()


def test_store_refuses_a_non_numeric_query_vector():
    """A garbage element must be an error, not a silent empty answer."""
    st = object.__new__(IRISGraphStore)
    st.conn = MagicMock()
    result = st.execute_knn_vec([Literal(value=1.0), Literal(value=0.0)], 3, None)
    assert result.rows == []
    assert result.error, "a non-numeric query vector answered as if it had been searched"
    st.conn.cursor.return_value.execute.assert_not_called()
