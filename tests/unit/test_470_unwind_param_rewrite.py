"""4.7.0 row 22: `UNWIND $p AS e` over a parameter list that mixes a list, map or boolean with a scalar of another kind
is read as `WITH $p AS __l UNWIND __l AS e`, which tells each element's kind (CY_LIST_KINDS), offline.

`UNWIND $p AS e RETURN e` with `$p = ['[2]', [2], true, '1']` answered `[2], [2], '1', '1'` (JSON_TABLE reads a nested
list as its text and a boolean as 1, and the parameter's elements were matched by text); the WITH form answers
`'[2]', [2], true, '1'`, measured on both routes. Only a list or map beside a scalar is rewritten (scalars beside
booleans already answered as themselves, R273's flag): the WITH form reads a long list by
position, 45x slower on 5,000 integers (1.36 s against 0.03 s), so a list of one kind (a batch of maps, of ids) keeps
the JSON_TABLE read. Live: tests/e2e/test_470_unwind_param_rewrite_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import rewrite_ambiguous_param_unwind, translate_to_sql

AMBIGUOUS = ["[2]", [2], True, "1"]


def _sql(q: str, params) -> str:
    """The SQL execute_cypher runs: it rewrites the caller's query, then translates it."""
    s = translate_to_sql(rewrite_ambiguous_param_unwind(parse_query(q), params), dict(params)).sql
    return s if isinstance(s, str) else "\n".join(s)


@pytest.mark.parametrize("value", [
    AMBIGUOUS,
    ["a", [1]],
    ["x", {"k": 1}],
    [True, [1]],
])
def test_an_ambiguous_parameter_list_is_read_as_a_with(value):
    q = "UNWIND $p AS e RETURN e"
    assert _sql(q, {"p": value}) == _sql("WITH $p AS __l0 UNWIND __l0 AS e RETURN e", {"p": value})
    assert "CY_LIST_KINDS" in _sql(q, {"p": value})


@pytest.mark.parametrize("value", [
    [1, 2, 3],
    ["a", "b"],
    [{"id": "a", "v": 1}, {"id": "b", "v": [2]}],  # a batch of maps: one kind
    [[1], [2, 3]],
    [True, False, None],
    [1, True],  # scalars and booleans carry R273's flag already: answered 1, true before
    [False, "0"],
    [[1], {"k": 1}],
    [],
    "not a list",
    None,
])
def test_a_list_of_one_kind_keeps_its_read(value):
    q = "UNWIND $p AS e RETURN e"
    assert "CY_LIST_KINDS" not in _sql(q, {"p": value})
    tree = parse_query(q)
    assert rewrite_ambiguous_param_unwind(tree, {"p": value}) is tree


@pytest.mark.parametrize("q", [
    "MATCH (n) UNWIND $p AS e RETURN n, e",  # a WITH would drop n
    "UNWIND [1] AS x UNWIND $p AS e RETURN x, e",
    "WITH 1 AS x UNWIND $p AS e RETURN x, e",
    # a write: the WITH form's CREATE fails -119 (rdf_props), where this one stores each element
    "UNWIND $p AS e CREATE (:X {v: e})",
    "UNWIND $p AS e MATCH (n:X {id: 'a'}) SET n.v = e RETURN n.v",
    "UNWIND $p AS e MERGE (n:X {id: 'a'}) RETURN e",
])
def test_an_unwind_after_another_clause_is_left_alone(q):
    tree = parse_query(q)
    assert rewrite_ambiguous_param_unwind(tree, {"p": AMBIGUOUS}) is tree


def test_the_rest_of_the_query_follows_the_unwind():
    q = "UNWIND $p AS e WITH e WHERE e <> '1' MATCH (n:X {id: $q}) RETURN e, n.id, $q AS q"
    got = rewrite_ambiguous_param_unwind(parse_query(q), {"p": AMBIGUOUS, "q": "a"})
    want = parse_query("WITH $p AS __l0 UNWIND __l0 AS e WITH e WHERE e <> '1' MATCH (n:X {id: $q}) "
                       "RETURN e, n.id, $q AS q")
    assert repr(got) == repr(want)


def test_the_parsed_query_is_not_changed():
    """parse_query is cached: the rewrite builds a new tree."""
    q = "UNWIND $p AS e RETURN e"
    before = repr(parse_query(q))
    rewrite_ambiguous_param_unwind(parse_query(q), {"p": AMBIGUOUS})
    assert repr(parse_query(q)) == before


def test_the_name_is_free():
    """A query already using __l0 gets the next name."""
    got = rewrite_ambiguous_param_unwind(parse_query("UNWIND $p AS e RETURN e, $__l0 AS z"),
                                         {"p": AMBIGUOUS, "__l0": 1})
    assert "__l1" in repr(got) and "alias='__l0'" not in repr(got)


def test_the_rewrite_is_idempotent():
    once = rewrite_ambiguous_param_unwind(parse_query("UNWIND $p AS e RETURN e"), {"p": AMBIGUOUS})
    assert rewrite_ambiguous_param_unwind(once, {"p": AMBIGUOUS}) is once


def test_the_native_cache_keys_the_two_reads_apart():
    """The native route caches a translation by repr(parsed) and the parameters' types: the rewrite happens before."""
    from iris_vector_graph._engine.query import QueryMixin as Q

    q = "UNWIND $p AS e RETURN e"
    plain = rewrite_ambiguous_param_unwind(parse_query(q), {"p": [1, 2]})
    mixed = rewrite_ambiguous_param_unwind(parse_query(q), {"p": AMBIGUOUS})
    assert Q._native_key(plain, {"p": [1, 2]}) != Q._native_key(mixed, {"p": AMBIGUOUS})


def test_translate_to_sql_leaves_a_tree_as_it_is():
    """Only the caller's query is rewritten (execute_cypher, /admin/explain): the write path translates trees of its
    own, and rewriting `UNWIND $p AS e WITH e CREATE ...`'s read step made the statements around it fail -23."""
    q = "UNWIND $p AS e RETURN e"
    s = translate_to_sql(parse_query(q), {"p": AMBIGUOUS}).sql
    assert "CY_LIST_KINDS" not in (s if isinstance(s, str) else "\n".join(s))


@pytest.mark.parametrize("q, rewritten", [
    ("UNWIND $p AS e WITH e WHERE e = true MATCH (n) SET n.f = e", True),  # a SET after a WITH reads the kinds
    ("UNWIND $p AS e WITH e MATCH (n {id: e}) REMOVE n.f", True),
    ("UNWIND $p AS e WITH e CREATE (:X {v: e})", False),  # the WITH form's CREATE fails -23
    ("UNWIND $p AS e WITH e WHERE e <> true MERGE (:X {m: e})", False),
    # a DELETE after a WITH reads the kinds too: it compared true as its text 1 and deleted the node whose id is '1'
    ("UNWIND $p AS e WITH e MATCH (n {id: e}) DETACH DELETE n", True),
    ("UNWIND $p AS e MATCH (n {id: e}) SET n.f = e", False),  # runs row by row from the list: reads each as itself
])
def test_which_writes_are_rewritten(q, rewritten):
    tree = parse_query(q)
    assert (rewrite_ambiguous_param_unwind(tree, {"p": AMBIGUOUS}) is not tree) is rewritten
