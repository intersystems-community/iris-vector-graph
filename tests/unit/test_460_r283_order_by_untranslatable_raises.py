"""4.6.0 R283: an ORDER BY item the translator cannot translate raises instead of being dropped.

`_to_sql_handle_with` caught any failure translating a WITH ... ORDER BY item and went on without it, so the rows came
back unsorted: `MATCH (n:L) WITH n ORDER BY nosuchfn(n.x) RETURN n.x` translated to SQL with no ORDER BY at all.
`preprocess_order_by` did the same when re-translating a RETURN alias the ORDER BY sorts on.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher import translator as t
from iris_vector_graph.cypher.parser import parse_query


def _translate(query):
    return t.translate_to_sql(parse_query(query), {}, engine=None)


@pytest.mark.parametrize("query, message", [
    ("MATCH (n:L) WITH n ORDER BY nosuchfn(n.x) RETURN n.x", "Unknown function 'nosuchfn'"),
    ("MATCH (n:L) WITH n ORDER BY n.x + nosuchfn(1) RETURN n.x", "Unknown function 'nosuchfn'"),
    ("MATCH (n:L) WITH n ORDER BY exists((n)-->()) RETURN n.x", "Pattern expression is not allowed"),
])
def test_a_with_order_by_item_that_fails_to_translate_raises(query, message):
    with pytest.raises(SyntaxError, match=message):
        _translate(query)


def test_a_with_order_by_that_translates_still_sorts():
    sql = _translate("MATCH (n:L) WITH n ORDER BY n.x DESC RETURN n.x").sql
    assert "ORDER BY" in sql and "DESC" in sql


def test_a_return_alias_the_order_by_sorts_on_raises_when_it_cannot_be_read(monkeypatch):
    """`RETURN n AS m` projects m_id, m_labels, m_props, so `ORDER BY m` re-translates `n`; a failure there raises."""
    original = t.translate_expression
    inside = {"on": False}

    def failing(expr, context, *a, **k):
        if inside["on"] and getattr(expr, "name", None) == "n":
            raise RuntimeError("cannot read n here")
        return original(expr, context, *a, **k)

    original_pob = t.preprocess_order_by

    def pob(query, context):
        inside["on"] = True
        try:
            return original_pob(query, context)
        finally:
            inside["on"] = False

    monkeypatch.setattr(t, "translate_expression", failing)
    monkeypatch.setattr(t, "preprocess_order_by", pob)
    with pytest.raises(RuntimeError, match="cannot read n here"):
        _translate("MATCH (n:L) RETURN n AS m ORDER BY m")
    # an alias the ORDER BY does not name is not needed: no raise
    _translate("MATCH (n:L) RETURN n AS m, n.x AS x ORDER BY x")
