"""4.7.2, live: the index hints are never written into a string literal of the user's.

4.7.0's `_hint_value_key_lookups` and `_hint_row_guards` scan the finished SQL for `(SELECT ` and for a guard's
`NOT EXISTS (SELECT 1 FROM ...` and count parentheses without skipping quoted text. A value that spells such SQL
(`(SELECT x FROM Graph_KG.nodes z WHERE z.val_key = 'q')`) had the `%IGNOREINDEX` text put inside it: a SET to the node
that value names wrote nothing (it answered the row), and a MERGE on it made a duplicate every run, with no error.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns463

pytestmark = [pytest.mark.e2e, *ns463.requires_namespace]

LABEL = "T472H"
SELECT_LITERAL = "(SELECT x FROM Graph_KG.nodes z WHERE z.val_key = 'q')"
GUARD_LITERAL = "NOT EXISTS (SELECT 1 FROM Graph_KG.rdf_props WHERE s = 1)"
UNBALANCED = "a ) b ( (SELECT"
LITERALS = [SELECT_LITERAL, GUARD_LITERAL, UNBALANCED]
IDS = ["select", "guard", "unbalanced"]


def _cypher_string(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


@pytest.fixture()
def engines():
    conn = ns463.connect()
    engs = ns463.engines(conn)
    ns463.rows(engs["sql"], f"MATCH (n:{LABEL}) DETACH DELETE n")
    conn.commit()
    yield engs
    ns463.rows(engs["sql"], f"MATCH (n:{LABEL}) DETACH DELETE n")
    conn.commit()
    conn.close()


@pytest.mark.parametrize("literal", LITERALS, ids=IDS)
def test_a_set_on_a_node_found_by_a_literal_that_spells_sql_writes(engines, literal):
    eng = engines["sql"]
    ns463.rows(eng, f"CREATE (:{LABEL} {{id: 'c1', name: $n}})", {"n": literal})
    eng.conn.commit()
    got = ns463.rows(eng, f'MATCH (n:{LABEL} {{name: "{_cypher_string(literal)}"}}) SET n.f = 1 RETURN n.id')
    eng.conn.commit()
    assert got == [["c1"]]
    # the stored row, not the row the statement answered
    assert ns463.rows(eng, f"MATCH (n:{LABEL}) RETURN n.id, n.f, n.name") == [["c1", 1, literal]]


@pytest.mark.parametrize("literal", LITERALS, ids=IDS)
def test_a_merge_on_a_literal_that_spells_sql_makes_one_node(engines, literal):
    eng = engines["sql"]
    for _ in range(3):
        got = ns463.rows(eng, f'MERGE (n:{LABEL} {{name: "{_cypher_string(literal)}"}}) RETURN n.name')
        eng.conn.commit()
        assert got == [[literal]]
    assert ns463.rows(eng, f"MATCH (n:{LABEL}) RETURN count(n)") == [[1]]


def test_a_create_keeps_a_value_that_spells_a_guard(engines):
    eng = engines["sql"]
    ns463.rows(eng, f'CREATE (:{LABEL} {{id: "c2", name: "{_cypher_string(GUARD_LITERAL)}"}})')
    eng.conn.commit()
    assert ns463.rows(eng, f"MATCH (n:{LABEL}) RETURN n.name") == [[GUARD_LITERAL]]
