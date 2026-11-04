"""4.6.0 R212: MERGE ON MATCH after a MATCH writes the node the MERGE created only when a later row matches it.

openCypher runs MERGE once per incoming row and each run sees what the earlier ones wrote, so the node the first row
creates is matched by the rows after it (TCK Merge3 [4], Merge4 [1]); with one row nothing matches it, and ON MATCH
writes only a node that was there before (Merge7 [1]/[2] for a relationship). After a one-row MATCH, ON MATCH wrote
the node the MERGE had just created.

Each case builds its own P nodes (labels unique to the run), merges `(c:C<run>)` and reads back what is stored, worked
by hand from the rule (a node there before: ON MATCH; the node MERGE creates: ON MATCH only if more than one row):

- one P, no C: one C, no `hit` / no `Hit` label; with ON CREATE as well, `{made: 1}`
- two P, no C: one C, `hit` 1 / labels C and Hit; with ON CREATE as well, `{made: 1, hit: 1}`
- one P, a C there before: still one C, `hit` 1
- two P, a C there before: still one C, `hit` 1
- one P through a WITH, no C: one C, no `hit`; two P through a WITH: `hit` 1
- no P: no C (MERGE runs for no row); OPTIONAL MATCH with no P: one null row, one C, no `hit`
"""

from __future__ import annotations

import contextlib
import json
import uuid

import pytest

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def g(iris_connection):
    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(iris_connection)
    labels: list = []

    def run_q(query, params=None):
        r = e.execute_cypher(query, dict(params or {}))
        assert r.error is None, (query, r.error)
        return r.rows

    def case(n_p, c_before=False):
        """`n_p` nodes labelled P<run>, and a C<run> node when `c_before`; returns (P label, C label)."""
        run = uuid.uuid4().hex[:8]
        p, c = f"P{run}", f"C{run}"
        labels.extend([p, c])
        for i in range(n_p):
            assert e.create_node(f"r212_{run}_p{i}", labels=[p], properties={})
        if c_before:
            assert e.create_node(f"r212_{run}_c", labels=[c], properties={})
        return p, c

    with contextlib.suppress(Exception):
        iris_connection.commit()
    yield run_q, case
    for label in labels:
        run_q(f"MATCH (n:{label}) DETACH DELETE n")


def _c(run_q, c):
    """[(labels, properties)] of every C node."""
    rows = run_q(f"MATCH (n:{c}) RETURN labels(n), properties(n)")
    return [(sorted(json.loads(lb)), json.loads(pr)) for lb, pr in rows]


@pytest.mark.parametrize(
    "n_p, c_before, want_hit",
    [(1, False, None), (2, False, 1), (1, True, 1), (2, True, 1)],
    ids=["one_row_created", "two_rows_created", "one_row_existing", "two_rows_existing"],
)
def test_on_match_property(g, n_p, c_before, want_hit):
    run_q, case = g
    p, c = case(n_p, c_before)
    run_q(f"MATCH (p:{p}) MERGE (c:{c}) ON MATCH SET c.hit = 1")
    got = _c(run_q, c)
    assert len(got) == 1, got
    assert got[0][1].get("hit") == want_hit, got


@pytest.mark.parametrize("n_p, want", [(1, ["C"]), (2, ["C", "Hit212"])], ids=["one_row", "two_rows"])
def test_on_match_label(g, n_p, want):
    run_q, case = g
    p, c = case(n_p)
    run_q(f"MATCH (p:{p}) MERGE (c:{c}) ON MATCH SET c:Hit212")
    got = _c(run_q, c)
    assert [lb for lb, _ in got] == [sorted(c if x == "C" else x for x in want)], got


@pytest.mark.parametrize("n_p, want", [(1, {"made": 1}), (2, {"made": 1, "hit": 1})], ids=["one_row", "two_rows"])
def test_on_create_and_on_match(g, n_p, want):
    run_q, case = g
    p, c = case(n_p)
    run_q(f"MATCH (p:{p}) MERGE (c:{c}) ON CREATE SET c.made = 1 ON MATCH SET c.hit = 1")
    got = _c(run_q, c)
    assert [pr for _, pr in got] == [want], got


@pytest.mark.parametrize("n_p, want_hit", [(1, None), (2, 1)], ids=["one_row", "two_rows"])
def test_through_a_with(g, n_p, want_hit):
    run_q, case = g
    p, c = case(n_p)
    run_q(f"MATCH (p:{p}) WITH p MERGE (c:{c}) ON MATCH SET c.hit = 1")
    got = _c(run_q, c)
    assert len(got) == 1 and got[0][1].get("hit") == want_hit, got


def test_no_row_merges_nothing(g):
    """No P: MERGE runs for no row and creates no C (it created one)."""
    run_q, case = g
    p, c = case(0)
    run_q(f"MATCH (p:{p}) MERGE (c:{c}) ON MATCH SET c.hit = 1")
    assert _c(run_q, c) == []
    run_q(f"MATCH (p:{p}) WITH p MERGE (c:{c})")
    assert _c(run_q, c) == []


def test_optional_match_null_row_merges_once(g):
    """No P, OPTIONAL MATCH: one null row, so MERGE runs once and creates one C, which ON MATCH does not write."""
    run_q, case = g
    p, c = case(0)
    run_q(f"OPTIONAL MATCH (p:{p}) MERGE (c:{c}) ON MATCH SET c.hit = 1")
    got = _c(run_q, c)
    assert len(got) == 1 and "hit" not in got[0][1], got


def test_merge_by_id_writes_only_its_node(g):
    """R214, `MERGE (n:K {id: ...})`: ON CREATE wrote every node lacking the key, ON MATCH wrote nothing. By hand: the
    first MERGE creates k with c = 1; a second matches it, m = 5 and c stays 1; a node of another label never gets c."""
    run_q, case = g
    p, c = case(0)
    kid = f"k_{c}"
    run_q(f"CREATE (:{p} {{id: 'other_{c}'}})")
    q = f"MERGE (n:{c} {{id: '{kid}'}}) ON CREATE SET n.c = 1 ON MATCH SET n.m = 5"
    run_q(q)
    assert _c(run_q, c) == [([c], {"id": kid, "c": 1})]
    run_q(q)
    assert _c(run_q, c) == [([c], {"id": kid, "c": 1, "m": 5})]
    assert run_q(f"MATCH (o:{p}) RETURN o.c") == [[None]]


@pytest.mark.parametrize("n_p, want", [(1, {"c": 1}), (2, {"c": 1, "m": 5})], ids=["one_row", "two_rows"])
def test_merge_by_id_after_a_match(g, n_p, want):
    """The rows rule for a node MERGE names by id: the first row creates it, a second matches it."""
    run_q, case = g
    p, c = case(n_p)
    kid = f"k_{c}"
    run_q(f"MATCH (p:{p}) MERGE (n:{c} {{id: '{kid}'}}) ON CREATE SET n.c = 1 ON MATCH SET n.m = 5")
    assert _c(run_q, c) == [([c], dict(want, id=kid))]
