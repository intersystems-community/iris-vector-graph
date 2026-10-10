"""Ledger.Records (SQL name ledger_records) and CanonicalRecord, the per-record export.

A changeset with a node, a label, a property and a relationship is committed, then its records are
read three ways: through the SQL table function, page by page, and one at a time as canonical JSON.
"""

from __future__ import annotations

import json
import os

import pytest

from iris_vector_graph.ledger import Changeset
from tests.integration._ledger_helpers import make_engine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def committed(iris_connection, ledger_reset):
    e = make_engine(iris_connection)
    e.ledger.enable()
    cs = Changeset(actor="t", actor_type="human")
    cs.create_node("rq-a", labels=["Lbl"], properties={"k": "v"})
    cs.create_node("rq-b")
    cs.create_relationship("rq-a", "R", "rq-b", qualifiers={"weight": "2"})
    res = e.ledger.commit(cs)
    return e, res.revision.seq


def records(conn, lo, hi, ord_from=1, page=5000):
    cur = conn.cursor()
    cur.execute("SELECT * FROM Graph_KG.ledger_records(?, ?, ?, ?)", [lo, hi, ord_from, page])
    cols = [d[0].lower() for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def test_records_lists_every_operation_of_a_revision_in_order(committed, iris_connection):
    _, seq = committed
    rows = records(iris_connection, seq, seq)
    assert rows, "the commit wrote no records"
    assert [r["ordinal"] for r in rows] == sorted(r["ordinal"] for r in rows)
    assert {r["seq"] for r in rows} == {seq}
    assert {"create_node", "add_label", "set_prop", "create_rel"} & {r["op"] for r in rows}
    assert any(r["entity_id"] == "rq-a" for r in rows)


def test_records_pages_and_resumes_from_an_ordinal(committed, iris_connection):
    _, seq = committed
    everything = records(iris_connection, seq, seq)
    first = records(iris_connection, seq, seq, 1, 2)
    assert len(first) == 2 and first == everything[:2]
    rest = records(iris_connection, seq, seq, first[-1]["ordinal"] + 1, 5000)
    assert first + rest == everything


def test_records_of_a_range_with_no_revisions_is_empty(committed, iris_connection):
    _, seq = committed
    assert records(iris_connection, seq + 100, seq + 200) == []


def test_canonical_record_is_the_record_as_sorted_key_json(committed, iris_connection):
    e, seq = committed
    rows = records(iris_connection, seq, seq)
    obj = e._iris_obj()
    for r in rows[:3]:
        raw = str(obj.classMethodValue("Graph.KG.Ledger", "CanonicalRecord", seq, r["ordinal"]))
        rec = json.loads(raw)
        assert rec["seq"] == seq and rec["ordinal"] == r["ordinal"]
        assert rec["op"] == r["op"] and rec["entity_id"] == r["entity_id"]
        assert list(rec) == sorted(rec), "canonical JSON has sorted keys"
    assert str(obj.classMethodValue("Graph.KG.Ledger", "CanonicalRecord", seq, 99999)) in ("", "None")
