"""4.7.2: the two index-hint rewrites of finished SQL (`_hint_row_guards`, `_hint_value_key_lookups`) read only the
SQL's own text; a quoted literal, the user's value, is never a place to put a hint nor a thing to count parentheses in.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher import translator as t

NODES = t._table("nodes")
PROPS = t._table("rdf_props")
LABELS = t._table("rdf_labels")
HINT = f"%IGNOREINDEX {PROPS}.idx_props_key, {LABELS}.idx_labels_label "
GUARD_HINT = f"%IGNOREINDEX {PROPS}.idx_props_key, {PROPS}.idx_props_key_valkey {PROPS}"

SELECT_LITERAL = "(SELECT x FROM " + NODES + " z WHERE z.val_key = ''q'')"  # as SQL spells it: quotes doubled
GUARD_LITERAL = "NOT EXISTS (SELECT 1 FROM " + PROPS + " WHERE s = 1)"


def _lookup(value_sql: str) -> str:
    """A matched node's property equality, the shape the translator writes."""
    return (
        f"UPDATE {PROPS} SET val = ? WHERE s IN (SELECT n0.node_id FROM {NODES} n0 "
        f"JOIN {PROPS} p2 ON p2.s = n0.node_id AND p2.\"key\" = ? "
        f"WHERE p2.val = {value_sql} AND p2.val_key = 'abc') AND \"key\" = ?"
    )


def test_a_lookup_is_hinted():
    got = t._hint_value_key_lookups(_lookup("?"))
    assert f"FROM {HINT}{NODES} n0" in got


@pytest.mark.parametrize(
    "literal",
    [
        f"CAST('{SELECT_LITERAL}' AS VARCHAR(256))",
        f"CAST('{GUARD_LITERAL}' AS VARCHAR(256))",
        "CAST('a ) b ( (SELECT' AS VARCHAR(256))",
    ],
    ids=["select", "guard", "unbalanced"],
)
def test_a_literal_is_left_as_written_and_the_lookup_around_it_is_hinted(literal):
    sql = _lookup(literal)
    got = t._hint_value_key_lookups(t._hint_row_guards(sql))
    assert literal in got, "the user's literal was changed"
    assert got.count("%IGNOREINDEX") == 1
    assert f"FROM {HINT}{NODES} n0" in got


def test_a_literal_that_spells_a_subquery_is_not_a_lookup():
    # the statement has no lookup of its own: the only val_key and SELECT are inside the quotes
    sql = f"INSERT INTO {PROPS} (s, \"key\", val) SELECT ?, ?, '(SELECT a FROM {NODES} z WHERE z.val_key = ''q'')' FROM {NODES}"
    assert t._hint_value_key_lookups(sql) == sql


def test_a_guard_spelled_in_a_literal_is_not_a_guard():
    sql = f"INSERT INTO {PROPS} (s, \"key\", val) SELECT ?, ?, '{GUARD_LITERAL}' WHERE 1 = 1"
    assert t._hint_row_guards(sql) == sql


def test_a_real_guard_after_a_literal_is_hinted():
    guard = f"NOT EXISTS (SELECT 1 FROM {PROPS} WHERE s = ? AND \"key\" = ?)"
    sql = f"INSERT INTO {PROPS} (s, \"key\", val) SELECT ?, ?, '{GUARD_LITERAL}' WHERE {guard}"
    got = t._hint_row_guards(sql)
    assert got.count("%IGNOREINDEX") == 1
    assert f"WHERE NOT EXISTS (SELECT 1 FROM {GUARD_HINT} WHERE s = ?" in got
    assert f"'{GUARD_LITERAL}'" in got


def test_a_doubled_quote_does_not_end_the_literal_early():
    # `''` inside a literal is a quote; the `)` after it is still the literal's
    sql = _lookup("'it''s (SELECT 1) ok'")
    got = t._hint_value_key_lookups(sql)
    assert "'it''s (SELECT 1) ok'" in got
    assert got.count("%IGNOREINDEX") == 1
