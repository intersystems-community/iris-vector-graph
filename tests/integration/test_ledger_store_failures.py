"""A ledger commit whose store refuses a write must leave the ledger and the graph untouched.

A BEFORE INSERT trigger that sets %ok=0 makes one table refuse new rows; the commit has
to fail as a whole (nothing recorded, head unchanged) and succeed again once the trigger
is gone. The trigger is dropped in a finally block so a failed assertion cannot leave it behind.
"""

from __future__ import annotations

import contextlib
import os

import pytest

from iris_vector_graph.ledger import Changeset
from iris_vector_graph.ledger.errors import LedgerError
from tests.integration._ledger_helpers import canonical_tables, make_engine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def engine(iris_connection, ledger_reset):
    e = make_engine(iris_connection)
    e.ledger.enable()
    return e


@contextlib.contextmanager
def refusing_inserts(conn, table):
    name = f"ivg_refuse_{table}"
    cur = conn.cursor()
    cur.execute(
        f"CREATE TRIGGER Graph_KG.{name} BEFORE INSERT ON Graph_KG.{table} "
        'LANGUAGE OBJECTSCRIPT { Set %ok=0 Set %msg="refused" }'
    )
    conn.commit()
    try:
        yield
    finally:
        conn.rollback()
        conn.cursor().execute(f"DROP TRIGGER Graph_KG.{name}")
        conn.commit()


def changeset():
    cs = Changeset(actor="t", actor_type="human")
    cs.create_node("fx-a", labels=["Lbl"], properties={"k": "v"})
    cs.create_node("fx-b")
    cs.create_relationship("fx-a", "R", "fx-b", qualifiers={"weight": "2"})
    return cs


@pytest.mark.parametrize("table", ["nodes", "rdf_labels", "rdf_props", "rdf_edges"])
def test_a_refused_insert_fails_the_whole_commit(engine, iris_connection, table):
    head = engine.ledger.head().revision_id
    with refusing_inserts(iris_connection, table):
        with pytest.raises(Exception) as caught:
            engine.ledger.commit(changeset())
    assert isinstance(caught.value, LedgerError) or "refused" in str(caught.value)
    assert engine.ledger.head().revision_id == head, "a failed commit moved the head"
    assert "fx-a" not in canonical_tables(iris_connection)["nodes"]
    # and the same changeset commits once the table accepts rows again
    assert engine.ledger.commit(changeset()).revision.seq == 2
