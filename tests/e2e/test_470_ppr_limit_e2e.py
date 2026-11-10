"""4.7.0 row 9, live: ``CALL ivg.ppr ... RETURN ... [ORDER BY score DESC] LIMIT k`` answers the rows it answered
without the LIMIT, cut at k, now that the LIMIT reaches kg_PPR (whose zeros, without it, filled 1000 rows).

The graph is the default graph: s -> t and eight nodes with no edge. From s: s = 1 / 1.85, t = 0.85 / 1.85, and the
eight at 0.0 (hand-worked; tests/e2e/test_470_ppr_zero_order_e2e.py checks the same graph against the routes). Under
``ORDER BY score DESC`` the rows past t tie at 0.0, so which loners a cut keeps is any of them, as without the
pushdown; the scores, the row count and the first two rows are fixed.

    IVG_NAMESPACE=IVG470ALG2 IVG_PORT=31972 IVG_TEST_CONTAINER=ivg-iris-enterprise \\
        pytest tests/e2e/test_470_ppr_limit_e2e.py
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463
from tests.e2e.ns470alg import conn, load  # noqa: F401 - conn is a fixture

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

LONERS = ["10", "9", "07", "a", "B", "-1", "1.5", "x"]
SCORES = {"s": 1 / 1.85, "t": 0.85 / 1.85, **{z: 0.0 for z in LONERS}}
CALL = "CALL ivg.ppr(['s'], 0.85, 100) YIELD node, score "


@pytest.fixture(scope="module")
def engs(conn):  # noqa: F811
    return load(conn, ["s", "t"] + LONERS, [("s", "R", "t")])


def _rows(eng, tail, params=None):
    return eng.execute_cypher(CALL + tail, dict(params or {}), raise_on_error=True).rows


def _check(rows, n, *, first=()):
    assert len(rows) == n, rows
    ids = [r[0] for r in rows]
    assert len(set(ids)) == n, f"a node twice: {ids}"
    for node, score in rows:
        assert score == pytest.approx(SCORES[node], abs=1e-4), (node, score)
    assert ids[: len(first)] == list(first)


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("k", [1, 2, 3, 5, 10, 20])
def test_order_by_score_desc_limit(engs, route, k):
    rows = _rows(engs[route], f"RETURN node.node_id AS id, score ORDER BY score DESC LIMIT {k}")
    _check(rows, min(k, 10), first=["s", "t"][:k])
    assert [r[1] for r in rows] == sorted((r[1] for r in rows), reverse=True)


@pytest.mark.parametrize("route", ns463.ROUTES)
@pytest.mark.parametrize("k", [1, 4, 10, 15])
def test_limit_without_order_by(engs, route, k):
    """Any k rows of the answer: each with its own score, none twice."""
    _check(_rows(engs[route], f"RETURN node.node_id AS id, score LIMIT {k}"), min(k, 10))


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_skip_and_limit(engs, route):
    rows = _rows(engs[route], "RETURN node.node_id AS id, score ORDER BY score DESC SKIP 1 LIMIT 3")
    _check(rows, 3, first=["t"])
    assert [r[1] for r in rows[1:]] == [0.0, 0.0]


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_parameter_limit(engs, route):
    _check(_rows(engs[route], "RETURN node.node_id AS id, score ORDER BY score DESC LIMIT $k", {"k": 2}), 2,
           first=["s", "t"])


@pytest.mark.parametrize("route", ns463.ROUTES)
def test_a_shape_that_keeps_the_full_answer_still_answers_right(engs, route):
    """ORDER BY score ascending: the zeros first, which a cut of the walk's answer at k would have dropped."""
    rows = _rows(engs[route], "RETURN node.node_id AS id, score ORDER BY score LIMIT 9")
    _check(rows, 9)
    assert [r[1] for r in rows[:8]] == [0.0] * 8
    assert rows[8][0] == "t"
