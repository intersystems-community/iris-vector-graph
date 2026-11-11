"""4.7.0 gate: a role without SELECT on the class dictionary (ADMIN_GUIDE §3's reading and writing roles) gets SQLCODE
-99 from `%Dictionary.CompiledIndex`. Row 16's adjacency check read it in SQL, so for such a user `status()` reported
"adjacency index probe failed ... Privilege violation" and the engine took the index for absent (the native executor
was skipped, and a reader's one-hop read went to SQL, -99 on CY_TYPED before setup). Refused there, the check asks
`Graph.KG.Adjacency.IndexKind` through the Native API, which any user may call (the answer is the same: functional,
plain or absent). Live: tests/e2e/test_460_r202_reading_role_e2e.py, test_460_r050_select_only_role_e2e.py."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.schema import GraphSchema

REFUSED = RuntimeError("<SQL ERROR>; Details: [SQLCODE: <-99>:<Privilege violation>]")


def _cursor(error=None, rows=None):
    cur = MagicMock()
    if error is not None:
        cur.execute.side_effect = error
    cur.fetchall.return_value = rows or []
    return cur


@pytest.mark.parametrize("answer", ["functional", "plain", "absent"])
def test_a_refused_dictionary_read_asks_the_class(answer):
    native = MagicMock()
    native.classMethodValue.return_value = answer
    assert GraphSchema.adjacency_index_kind(_cursor(REFUSED), native=native) == answer
    native.classMethodValue.assert_called_once_with("Graph.KG.Adjacency", "IndexKind", "Graph.KG.rdfedges")


def test_another_schema_names_its_own_table_class():
    native = MagicMock()
    native.classMethodValue.return_value = "functional"
    assert GraphSchema.adjacency_index_kind(_cursor(REFUSED), schema="IVG_Alt", native=native) == "functional"
    native.classMethodValue.assert_called_once_with("Graph.KG.Adjacency", "IndexKind", "IVG.Alt.rdfedges")


def test_without_a_native_handle_the_refusal_is_raised():
    with pytest.raises(RuntimeError, match="-99"):
        GraphSchema.adjacency_index_kind(_cursor(REFUSED))


def test_another_error_is_raised_and_the_class_is_not_asked():
    native = MagicMock()
    with pytest.raises(RuntimeError, match="no such table"):
        GraphSchema.adjacency_index_kind(_cursor(RuntimeError("no such table")), native=native)
    native.classMethodValue.assert_not_called()


def test_an_answer_the_class_cannot_give_is_not_guessed():
    """A namespace whose classes predate the method (<METHOD DOES NOT EXIST>) raises the original refusal."""
    native = MagicMock()
    native.classMethodValue.side_effect = RuntimeError("<METHOD DOES NOT EXIST>")
    with pytest.raises(RuntimeError, match="-99"):
        GraphSchema.adjacency_index_kind(_cursor(REFUSED), native=native)
