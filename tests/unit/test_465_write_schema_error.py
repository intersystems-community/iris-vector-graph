"""4.6.5: a write refused for a namespace whose classes predate 4.6.4 raises an exception that is both
``IVGSchemaError`` and ``GraphWriteError``.

docs/USER_GUIDE.md and the 4.6.4 CHANGELOG say a write inside the caller's transaction on such a namespace "raises
``IVGSchemaError`` before writing anything". ``_write_txn`` raises that, and the write's own wrapper turned it into a
``GraphWriteError`` (``create_edge failed, nothing written: Graph_KG.Txn_Begin is not in this namespace ...``), a class
unrelated to it: code written to the documented class missed it, and code catching ``GraphWriteError`` must keep
working. The two live write paths are checked in tests/e2e/test_465_write_schema_error_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.errors import GraphWriteError, GraphWriteSchemaError
from iris_vector_graph.schema_name import IVGSchemaError
from iris_vector_graph.sdk import IVGError

MESSAGE = "Graph_KG.Txn_Begin is not in this namespace (its IVG classes predate 4.6.4); nothing was written."


def test_the_wrapped_schema_error_is_both_classes():
    cause = IVGSchemaError(MESSAGE, recorded="Graph_KG", requested="IVGALT")
    err = GraphWriteError("create_edge", cause)
    assert isinstance(err, GraphWriteError) and isinstance(err, IVGSchemaError)
    assert isinstance(err, IVGError) and isinstance(err, RuntimeError)
    assert type(err) is GraphWriteSchemaError


def test_the_message_is_the_one_a_write_error_has_with_the_causes_text():
    err = GraphWriteError("create_edge", IVGSchemaError(MESSAGE))
    assert str(err) == f"create_edge failed, nothing written: {MESSAGE}"
    assert err.operation == "create_edge" and err.message == MESSAGE and err.sqlcode is None


def test_the_schema_errors_own_attributes_are_kept():
    err = GraphWriteError("delete_node", IVGSchemaError(MESSAGE, recorded="Graph_KG", requested="IVGALT"))
    assert (err.recorded, err.requested) == ("Graph_KG", "IVGALT")


def test_each_except_clause_the_docs_and_the_old_code_use_catches_it():
    for caught in (GraphWriteError, IVGSchemaError, IVGError, RuntimeError):
        with pytest.raises(caught):
            raise GraphWriteError("store_node", IVGSchemaError(MESSAGE))


def test_a_write_error_of_another_cause_is_no_schema_error():
    for cause in (RuntimeError("[SQLCODE: <-99>:<Privilege violation>]"), ValueError("bad"), IVGError("x")):
        err = GraphWriteError("create_edge", cause)
        assert type(err) is GraphWriteError and not isinstance(err, IVGSchemaError)


def test_a_cypher_refusal_keeps_its_plain_message():
    err = GraphWriteError("execute_cypher", IVGSchemaError(MESSAGE), plain=True)
    assert isinstance(err, IVGSchemaError) and str(err) == MESSAGE


def test_the_cause_chain_survives():
    cause = IVGSchemaError(MESSAGE)
    try:
        try:
            raise cause
        except IVGSchemaError as exc:
            raise GraphWriteError("create_edge", exc) from exc
    except GraphWriteError as err:
        assert err.__cause__ is cause


def test_a_subclass_of_the_write_error_is_left_as_it_is():
    class Mine(GraphWriteError):
        pass

    assert type(Mine("op", IVGSchemaError(MESSAGE))) is Mine
