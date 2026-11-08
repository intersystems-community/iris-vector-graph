"""4.6.3: kg_PAGERANK and delete_node raise IVG's errors for a call IRIS refuses, naming the method, the cause chained.

On a user without SELECT, `kg_PAGERANK()` let the driver's `iris.dbapi.OperationalError` (SQLCODE -99) through raw, and
`delete_node` let the same through, or a plain `RuntimeError` for SQLCODE -415 (a name too long for the index's
global reference). The read raises `RuntimeError` naming `kg_PAGERANK`; the delete raises `GraphWriteError`. No IRIS
here; the live case is tests/e2e/test_463_refused_reads_e2e.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import iris.dbapi
import pytest

from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.errors import GraphWriteError, PowerIterationFailedConvergence
from iris_vector_graph.result import IVGResult

REFUSED = ("<SQL ERROR>; Details: [SQLCODE: <-99>:<Privilege violation>] "
           "[%msg: <User R is not privileged for the operation>]")
TOO_LONG = "<SQL ERROR>; Details: [SQLCODE: <-415>:<Fatal error occurred within the SQL filer>] <SUBSCRIPT>"


def _pagerank_engine(**overrides):
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng.conn = MagicMock()
    eng._store = MagicMock()
    eng._store.execute_pagerank.return_value = IVGResult(columns=["id", "score"], rows=[["a", 1.0]])
    eng._store_capabilities = {}
    eng._node_set_token = lambda: "1:1"
    eng._loners = lambda graph="": {}
    for name, value in overrides.items():
        setattr(eng, name, value)
    return eng


def test_kg_pagerank_wraps_a_driver_refusal():
    eng = _pagerank_engine()
    cause = iris.dbapi.OperationalError(REFUSED)
    eng._store.execute_pagerank.side_effect = cause
    with pytest.raises(RuntimeError, match=r"^kg_PAGERANK failed \(SQLCODE -99\): .*not privileged") as raised:
        eng.kg_PAGERANK()
    assert not isinstance(raised.value, iris.dbapi.Error)
    assert raised.value.__cause__ is cause


def test_kg_pagerank_wraps_a_refusal_on_its_fallback_path():
    eng = _pagerank_engine(_store_capabilities={"pagerank": False})
    cause = iris.dbapi.OperationalError(REFUSED)
    with patch("iris_vector_graph.schema._call_classmethod", side_effect=cause), \
            pytest.raises(RuntimeError, match=r"^kg_PAGERANK failed \(SQLCODE -99\)") as raised:
        eng.kg_PAGERANK()
    assert raised.value.__cause__ is cause


def test_kg_pagerank_keeps_ivg_errors_as_they_are():
    eng = _pagerank_engine()
    eng._store.execute_pagerank.side_effect = PowerIterationFailedConvergence("did not converge in 20 iterations")
    with pytest.raises(PowerIterationFailedConvergence):
        eng.kg_PAGERANK()


def test_kg_pagerank_still_answers():
    assert _pagerank_engine().kg_PAGERANK() == [("a", 1.0)]


def _delete_engine():
    conn = MagicMock()
    conn.cursor.return_value = MagicMock(fetchall=MagicMock(return_value=[]), fetchone=MagicMock(return_value=(0,)))
    return IRISGraphEngine(conn, embedding_dimension=4)


@pytest.mark.parametrize("cause", [iris.dbapi.OperationalError(REFUSED), RuntimeError(TOO_LONG)],
                         ids=["-99 driver error", "-415 RuntimeError"])
def test_delete_node_raises_graph_write_error_for_a_refused_delete(cause):
    eng = _delete_engine()
    with patch.object(eng, "_delete_scopes", return_value={"": ["a"]}), \
            patch.object(eng, "_reifiers_of", return_value=[]), \
            patch.object(eng._store, "delete_nodes", side_effect=cause), \
            pytest.raises(GraphWriteError, match=r"^delete_node failed \(SQLCODE -(99|415)\), nothing written") as r:
        eng.delete_node("a", graph=None)
    assert r.value.operation == "delete_node" and r.value.__cause__ is cause


def test_delete_node_raises_graph_write_error_for_a_refused_lookup():
    """With the graph omitted it first reads which graph holds the node; a user without SELECT is refused there."""
    eng = _delete_engine()
    cause = iris.dbapi.OperationalError(REFUSED)
    with patch.object(eng, "_graphs_holding", side_effect=cause), \
            pytest.raises(GraphWriteError, match=r"^delete_node failed \(SQLCODE -99\), nothing written") as raised:
        eng.delete_node("a")
    assert raised.value.__cause__ is cause


def test_delete_node_keeps_its_value_error_for_a_shared_id():
    eng = _delete_engine()
    with patch.object(eng, "_graphs_holding", return_value={"": ["a"], "g": ["a"]}), \
            pytest.raises(ValueError, match="more than one graph"):
        eng.delete_node("a")


def test_delete_node_says_what_stays_when_the_reifiers_fail():
    """The node's own delete is one Eraser transaction; its edges' reifier nodes go after it, in their graphs."""
    eng = _delete_engine()
    cause = RuntimeError(TOO_LONG)
    with patch.object(eng, "_delete_scopes", return_value={"": ["a"]}), \
            patch.object(eng, "_reifiers_of", return_value=["r1"]), \
            patch.object(eng, "_graphs_holding", return_value={"": ["r1"]}), \
            patch.object(eng._store, "delete_nodes",
                         side_effect=[IVGResult(columns=["deleted"], rows=[[1]]), cause]), \
            pytest.raises(GraphWriteError, match=r"delete_node failed \(SQLCODE -415\), the node is deleted; the "
                                                 r"reifier nodes of its edges \(1\) are not"):
        eng.delete_node("a", graph=None)
