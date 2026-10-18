"""CY_SORT_KEY as installed: the keys it gave before the regex was removed, and an
impossible date that orders as a string instead of failing the statement.

The expected keys were read from the previous (regex) body on SNB SF0.1; over all 1.47M
distinct property values there the two bodies agree.

Run against a scratch namespace, e.g. `IVG_NAMESPACE=TCKJ`.
"""

from __future__ import annotations

import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")

GOLDEN = {
    "2012-08-01T10:49:25.489+00:00": "f0063479414965489000000",
    "2012-08-01": "h2012-08-01",
    "2012-08-01T10:49": "g2012-08-0138940000000000",
    "2012-08-01X10:49": "l2012-08-01X10:49",
    "12:30": "j45000000000000",
    "12:30:15.5+01:00": "i141415500000000",
    "12x30": "i035200000000000",
    "12\n30": "l12\n30",
    "12\x0b30": "l12\x0b30",
    "12\x0c30": "l12\x0c30",
    "P1D": "kP1D",
    "-3": "n0499996~",
    "3.5": "n25000035",
    "true": "m1",
    "NaN": "o",
    "[1, 2]": "dn2500001\x01n2500002\x01",
    '{"a": 1}': 'a{"a": 1}',
    "2012-08-01T10:49:25[Europe/Paris]": "g2012-08-0138965000000000",
    "2012-08-01T10:49:25-05:30": "f0063479434765000000000",
    "10:49:25Z": "i138965000000000",
    "x": "lx",
    "007": "n2500007",
}


@pytest.fixture
def cursor(iris_connection):
    engine = IRISGraphEngine(iris_connection)
    cur = iris_connection.cursor()
    engine._install_procedures(cur)
    iris_connection.commit()
    return cur


def _key(cur, v):
    cur.execute("SELECT SQLUser.CY_SORT_KEY(?, 'x')", [v])
    return cur.fetchone()[0]


@pytest.mark.parametrize("value", sorted(GOLDEN))
def test_keys_are_unchanged(cursor, value):
    assert _key(cursor, value) == GOLDEN[value]


@pytest.mark.parametrize("value", ["2021-02-30T10:00", "2012-13-45T25:61:61Z"])
def test_an_impossible_date_orders_as_a_string(cursor, value):
    assert _key(cursor, value) == "l" + value


def test_order_by_over_an_impossible_date_returns_rows(iris_connection, iris_master_cleanup):
    engine = IRISGraphEngine(iris_connection)
    engine._install_procedures(iris_connection.cursor())
    for i, v in enumerate(["2021-02-28T10:00", "2021-02-30T10:00", "2021-03-01T10:00"]):
        engine.create_node(f"sk_{i}", labels=["SortProbe"], properties={"at": v})
    res = engine.execute_cypher("MATCH (n:SortProbe) RETURN n.at AS ts ORDER BY n.at")
    assert getattr(res, "error", None) is None
    assert [r[0] for r in res.rows] == ["2021-02-28T10:00", "2021-03-01T10:00", "2021-02-30T10:00"]
