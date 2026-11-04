"""4.6.0 R114: verify leaves a temporal-marked statement out of the replay, as it leaves the marked row out of the tables.

A genesis before 4.6.0 recorded a temporal edge's marked row (`{"temporal": true}`) as a statement with the qualifier
`temporal` = `"true"`; `read_canonical` skips that row. Replayed, the ledger held the statement and the tables did not,
so such a ledger diverged forever. The rule is now applied to both sides.
"""

from __future__ import annotations

from iris_vector_graph.ledger import replay as R


def _replayed():
    st = R.GraphState()
    for nid in ("n1", "n2"):
        st.nodes[nid] = R.NodeState()
    for sid, p, quals in (("1", "calls", {"temporal": "true"}), ("2", "KNOWS", {"since": "2020"})):
        stmt = R.StmtState("n1", p, "n2", None, quals)
        st.statements[sid] = stmt
        st._stmt_by_tuple[stmt.tuple] = sid
    return st


def _canonical():
    """The tables as read_canonical reads them: the marked `calls` row is skipped, the plain `KNOWS` one is not."""
    st = R.GraphState()
    for nid in ("n1", "n2"):
        st.nodes[nid] = R.NodeState()
    stmt = R.StmtState("n1", "KNOWS", "n2", None, {"since": "2020"})
    st.statements["2"] = stmt
    st._stmt_by_tuple[stmt.tuple] = "2"
    return st


def test_a_recorded_marked_statement_no_longer_diverges():
    replayed = _replayed()
    entries, _ = R.compare(replayed, _canonical())
    assert [e.entity_id for e in entries] == ["1"]  # what an old genesis's ledger reported
    R._drop_temporal_marked(replayed)
    assert R.compare(replayed, _canonical()) == ([], None)
    assert set(replayed.statements) == {"2"} and set(replayed._stmt_by_tuple.values()) == {"2"}
