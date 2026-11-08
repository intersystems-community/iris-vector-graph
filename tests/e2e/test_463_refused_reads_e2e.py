"""4.6.3: a call IRIS refuses for a missing privilege says so, in the log and in the error's type.

A user holding ADMIN_GUIDE §3's reading role without its SQL `SELECT` on the IVG Schema (the role's resources, and
`EXECUTE` only):

- a Cypher read answers its `error`, and the log said `execute_sql error` at DEBUG only: an application that logs at
  WARNING saw nothing. It is a WARNING now, with the SQLCODE and privilege text.
- `kg_PAGERANK()` let the driver's `iris.dbapi.OperationalError` (SQLCODE -99) through raw: a `RuntimeError` naming
  the method now, the driver's error chained.
- `delete_node` let the same `OperationalError` through: a `GraphWriteError` now, chained.

The expected answers are IRIS's: SQLCODE -99 is the privilege violation the guide's role without SELECT gets.

    IVG_R463_PRIV_NS=IVG463C pytest tests/e2e/test_463_refused_reads_e2e.py
"""

from __future__ import annotations

import logging
import os

import pytest

from tests.e2e import reading_role460 as rr
from tests.e2e import scratch_vars

NS = os.environ.get("IVG_R463_PRIV_NS", "")
TAG = "R463"

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not rr.container_running(), reason=f"{rr.CONTAINER} is not running"),
    scratch_vars.requires("IVG_R463_PRIV_NS"),
]


@pytest.fixture(scope="module")
def no_select():
    """(owner connection, connection of a user with the reading role's resources and EXECUTE but no SELECT)."""
    from iris_vector_graph import IRISGraphEngine

    sysconn = rr.connect(namespace=NS)
    owner = IRISGraphEngine(sysconn, embedding_dimension=rr.DIM)
    rr.seed(owner)
    user = rr.make_reader(TAG, rr.reading_resources(namespace=NS), sql_privileges=("EXECUTE",), namespace=NS)
    conn = rr.connect(user, rr.PASSWORD, namespace=NS)
    try:
        yield sysconn, conn
    finally:
        conn.close()
        rr.drop_reader(TAG, namespace=NS)
        owner.erase_graph(None)
        sysconn.close()


@pytest.fixture
def engine(no_select):
    from iris_vector_graph import IRISGraphEngine

    return IRISGraphEngine(no_select[1], embedding_dimension=rr.DIM)


def test_a_refused_cypher_read_logs_a_warning_naming_the_privilege(engine, caplog):
    with caplog.at_level(logging.DEBUG, logger="iris_vector_graph"):
        result = engine.execute_cypher("MATCH (n:P) RETURN n.node_id")
    assert result.error and "-99" in result.error, result.error
    warned = [r for r in caplog.records if r.levelno == logging.WARNING and "execute_sql" in r.getMessage()]
    assert warned, [(r.levelname, r.getMessage()[:120]) for r in caplog.records]
    message = warned[0].getMessage()
    assert "SQLCODE -99" in message and "Privilege" in message, message


def test_kg_pagerank_raises_runtime_error_naming_itself(engine):
    import iris.dbapi

    with pytest.raises(RuntimeError, match=r"kg_PAGERANK failed \(SQLCODE -99\)") as raised:
        engine.kg_PAGERANK()
    assert not isinstance(raised.value, iris.dbapi.Error)
    assert isinstance(raised.value.__cause__, iris.dbapi.Error), repr(raised.value.__cause__)


def test_delete_node_raises_graph_write_error_and_deletes_nothing(engine, no_select):
    import iris.dbapi

    from iris_vector_graph.errors import GraphWriteError

    with pytest.raises(GraphWriteError, match=r"delete_node failed \(SQLCODE -99\), nothing written") as raised:
        engine.delete_node("a")
    assert isinstance(raised.value.__cause__, iris.dbapi.Error), repr(raised.value.__cause__)
    cur = no_select[0].cursor()
    cur.execute("SELECT COUNT(*) FROM Graph_KG.nodes WHERE node_id = 'a'")
    assert int(cur.fetchone()[0]) == 1


def test_delete_node_of_an_id_too_long_for_the_index_raises_graph_write_error(no_select):
    """An id too long for a global subscript: the Eraser's <THROW> came through as a plain RuntimeError."""
    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph.errors import GraphWriteError

    owner = IRISGraphEngine(no_select[0], embedding_dimension=rr.DIM)
    with pytest.raises(GraphWriteError, match=r"^delete_node failed") as raised:
        owner.delete_node("x" * 900, graph=None)
    assert raised.value.operation == "delete_node" and raised.value.__cause__ is not None
