"""4.7.1 H1: a MERGE's existence check and its binding join compare a number property by the same rule.

The existence check (the `NOT EXISTS` sub-SELECT, aliases `_ml0`/`_mpN`) decides whether the MERGE creates; the binding
join (aliases `nN`/`pN`) answers the rows. 4.7.0 row 24 compares a node property with a number by the value's type
(a stored number of that value only); the existence check kept 4.6.5's spellings (`_mp0.val IN ('6', '6.0')`, which
the string '6' satisfies), so with '6' stored a `MERGE (n:M {v: 6})` neither created nor answered a row. The E2E
known answers are in tests/e2e/test_471_merge_number_e2e.py; this pins that the two predicates are one rule.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _sql(query, params=None):
    r = translate_to_sql(parse_query(query), params or {})
    return [re.sub(r"\s+", " ", s) for s in (r.sql if isinstance(r.sql, list) else [r.sql])]


def _value_predicate(stmt: str, alias_re: str) -> str:
    """The `v` property join's value test on the alias `alias_re` matches, with the alias written `A`."""
    m = re.search(rf'JOIN Graph_KG\.rdf_props ({alias_re}) ON .*?\1\."key" = \? AND (.*?)(?= JOIN | LEFT JOIN | WHERE |'
                  rf' AND COALESCE\(_ml0| AND _ml0|\)$)', stmt)
    assert m, stmt
    return m.group(2).replace(f"{m.group(1)}.", "A.")


@pytest.mark.parametrize("query, params", [
    ("MERGE (n:M {v: 6}) RETURN n.v", {}),
    ("MERGE (n:M {v: $v}) RETURN n.v", {"v": 6}),
    ("MERGE (n:M {v: 0.5}) RETURN n.v", {}),
    ("MERGE (n:M {v: 6}) ON CREATE SET n.c = 1 ON MATCH SET n.m = 1 RETURN n.v", {}),
], ids=["literal", "param", "float", "on_set"])
def test_the_existence_check_and_the_binding_agree(query, params):
    stmts = _sql(query, params)
    checks = [_value_predicate(s, r"_mp0") for s in stmts if "NOT EXISTS (SELECT 1 FROM Graph_KG.nodes _ml0" in s]
    binds = [_value_predicate(s, r"p\d+") for s in stmts if s.startswith("SELECT ")]
    assert checks and binds, stmts
    assert set(checks) == set(binds), (checks, binds)
    assert "$LISTBUILD(%EXACT(A.val))" in checks[0]  # row 24's type test, not the spellings


def test_a_string_merge_keeps_its_equality():
    stmts = _sql("MERGE (n:M {v: '6'}) RETURN n.v")
    checks = [_value_predicate(s, r"_mp0") for s in stmts if "NOT EXISTS (SELECT 1 FROM Graph_KG.nodes _ml0" in s]
    assert checks and set(checks) == {"A.val = ?"}, checks
