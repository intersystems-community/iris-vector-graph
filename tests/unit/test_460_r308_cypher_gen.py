"""4.6.0 R308, offline: the Cypher differential's generator (tests/oracle/cypher_gen.py) and its reference interpreter.

The live differential (tests/e2e/test_460_r308_cypher_differential_e2e.py) runs each generated query through every
route IVG has and compares the answers, and compares them to the interpreter's answer where it has one. These tests pin
what that rests on: a seed always gives the same graph and queries; every family is generated and every query parses
and translates; and the interpreter answers each hand case with the answer worked by hand (HAND_CASES, never IVG's
output), so a disagreement with IVG is IVG's or the hand case's, not an untested interpreter's.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from tests.oracle import cypher_gen as cg


@pytest.fixture(scope="module")
def graph():
    return cg.make_graph(7)


@pytest.fixture(scope="module")
def queries(graph):
    return cg.generate(graph, 7, 300)


def test_a_seed_gives_the_same_graph_and_queries(graph, queries):
    again = cg.make_graph(7)
    assert [(n.graph, n.id, n.labels, n.props) for n in again.nodes] == [
        (n.graph, n.id, n.labels, n.props) for n in graph.nodes
    ]
    assert [(e.graph, e.src, e.type, e.dst, e.props) for e in again.edges] == [
        (e.graph, e.src, e.type, e.dst, e.props) for e in graph.edges
    ]
    assert [(q.text, q.params) for q in cg.generate(again, 7, 300)] == [(q.text, q.params) for q in queries]
    other = cg.make_graph(8)
    assert [n.props for n in other.nodes] != [n.props for n in graph.nodes]


def test_the_graph_is_well_formed(graph):
    keys = {(n.graph, n.id) for n in graph.nodes}
    assert {n.graph for n in graph.nodes} == {"", graph.named}
    assert all(n.id.startswith(graph.id_prefix) for n in graph.nodes)
    assert all(n.props["id"] == n.id for n in graph.nodes)
    seen = set()
    for e in graph.edges:
        assert (e.graph, e.src) in keys and (e.graph, e.dst) in keys, e
        assert e.src != e.dst, e
        assert (e.graph, e.src, e.type, e.dst) not in seen, e
        seen.add((e.graph, e.src, e.type, e.dst))
    held = {type(v).__name__ for e in graph.edges for v in e.props.values()}
    assert {"int", "float", "str", "bool", "list", "dict"} <= held, held


def test_every_family_is_generated(queries):
    assert {q.family for q in queries} == set(cg.FAMILIES)


def test_every_query_and_form_parses_and_translates(queries):
    for q in queries:
        for text in [q.text, *q.forms]:
            try:
                translate_to_sql(parse_query(text, q.params), q.params)
            except Exception as ex:  # the failure names the query
                pytest.fail(f"{q.family} {text!r} {q.params}: {type(ex).__name__}: {ex}")


def test_every_generated_query_has_a_reference_answer(graph, queries):
    answers = [q.evaluate(graph) for q in queries]
    # None only where IVG's own representation is the answer (id(), elementId())
    assert all(a is not None or "id(" in q.text or "elementId(" in q.text for q, a in zip(queries, answers))
    assert sum(1 for a in answers if a is not None) > 0.9 * len(queries)
    # not vacuous: most queries answer some rows
    assert sum(1 for a in answers if a) > len(queries) // 2


def test_there_are_forty_hand_cases_or_more():
    assert len(cg.hand_cases()) >= 40
    names = [c.name for c in cg.hand_cases()]
    assert len(set(names)) == len(names)


@pytest.mark.parametrize("case", cg.hand_cases(), ids=lambda c: c.name)
def test_the_interpreter_gives_the_hand_answer(case):
    hand = cg.hand_graph()
    got = case.query.evaluate(hand)
    assert cg.same_rows(got, case.expected, case.query), (case.query.text, got, case.expected)


@pytest.mark.parametrize("case", cg.hand_cases(), ids=lambda c: c.name)
def test_every_hand_case_parses(case):
    parse_query(case.query.text, case.query.params)


# --- the value rules the comparison rests on ---


def test_equality_follows_cypher():
    assert cg.cy_eq(1, 1.0) is True
    assert cg.cy_eq([1], [1.0]) is True
    assert cg.cy_eq({"k": 1}, {"k": 1.0}) is True
    assert cg.cy_eq("1", 1) is False
    assert cg.cy_eq(True, 1) is False
    assert cg.cy_eq(None, 1) is None
    assert cg.cy_eq([1, None], [1, 2]) is None
    assert cg.cy_eq([1, None], [2, 2]) is False


def test_the_comparison_keeps_types_apart():
    q = cg.Query(text="", params={}, ordered=False, bag_cols=frozenset(), family="t", forms=[], _eval=lambda g: [])
    assert not cg.same_rows([("1",)], [(1,)], q)
    assert not cg.same_rows([(1,)], [(1.0,)], q)
    assert not cg.same_rows([(True,)], [(1,)], q)
    assert cg.same_rows([(0.1 + 0.2,)], [(0.3,)], q)
    assert cg.same_rows([(1,), (2,)], [(2,), (1,)], q)
    assert not cg.same_rows([(1,), (1,)], [(1,)], q)
    ordered = cg.Query(text="", params={}, ordered=True, bag_cols=frozenset(), family="t", forms=[], _eval=lambda g: [])
    assert not cg.same_rows([(1,), (2,)], [(2,), (1,)], ordered)
    bag = cg.Query(text="", params={}, ordered=False, bag_cols=frozenset({0}), family="t", forms=[], _eval=lambda g: [])
    assert cg.same_rows([(["a", "b"],)], [(["b", "a"],)], bag)
    assert not cg.same_rows([(["a", "b"],)], [(["b", "a"],)], q)


def test_a_trail_uses_no_relationship_twice():
    hand = cg.hand_graph()
    a, r = hand.labels[0], hand.types[0]
    # bob -R- ann and bob -R- cat; from ann the only R edge is the one walked in, so the answer is dan alone
    q = cg.Q(
        [cg.match(cg.pat(cg.N("a", a, name="bob"), cg.R(None, r, dir="both"), cg.N("b"), cg.R(None, r, dir="both"),
                         cg.N("c")))],
        cg.ret([(cg.prop("c", "name"), None)]),
    )
    assert q.evaluate(hand) == [("dan",)]
