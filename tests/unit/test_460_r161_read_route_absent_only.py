"""4.6.0 R161: `_read_route` treats the registry as unreadable only when `embedding_registry` does not exist.

It caught every exception, logged it at debug and answered `(False, None)`, "this database does not route", so a
read fell back to the legacy `kg_NodeEmbeddings`: a user without SELECT on the registry, or any other failure,
searched the default graph's vectors for a named graph and got real-looking scores from the wrong space
(`_route_for_read`), and a write went to the legacy table (`_route_for_write`). R146 fixed the same pattern in
`Graph.KG.PyOps`. Now only SQLCODE -30 (the table is absent: a namespace from before spec 226) answers
`(False, None)`; anything else raises.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine

ABSENT = RuntimeError("[SQLCODE: <-30>:<Table or view not found>] [Location: <Prepare>] Graph_KG.embedding_registry")
DENIED = RuntimeError("[SQLCODE: <-99>:<Privilege violation>] User R161 is not privileged for the operation")
OTHER = RuntimeError("[SQLCODE: <-29>:<Field not found in the applicable tables>] KIND")
OFFLINE = ConnectionError("connection reset by peer")


def _engine(error):
    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine.conn = MagicMock()
    engine._schema_prefix = "Graph_KG"
    engine._t = lambda name: f"Graph_KG.{name}"
    engine._registry_table = lambda: "Graph_KG.embedding_registry"
    engine.conn.cursor.return_value.execute.side_effect = error
    return engine


def test_an_absent_registry_is_unreadable():
    assert _engine(ABSENT)._read_route("g1", None) == (False, None)


@pytest.mark.parametrize("error", [DENIED, OTHER, OFFLINE], ids=["denied", "other", "offline"])
def test_any_other_failure_raises(error):
    with pytest.raises(type(error)) as raised:
        _engine(error)._read_route("g1", None)
    assert raised.value is error


@pytest.mark.parametrize("error", [DENIED, OTHER], ids=["denied", "other"])
def test_a_named_graph_read_does_not_fall_back_to_the_legacy_table(error):
    engine = _engine(error)
    engine._offered_embedding_identity = lambda config=None: MagicMock(model_key=None)
    with pytest.raises(RuntimeError):
        engine._route_for_read("g1", None)


def test_an_absent_registry_reads_the_legacy_table():
    engine = _engine(ABSENT)
    engine._offered_embedding_identity = lambda config=None: MagicMock(model_key=None)
    assert engine._route_for_read("g1", None) == ("kg_NodeEmbeddings", None)
