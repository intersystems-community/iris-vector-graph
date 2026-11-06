"""4.6.0 R310 follow-up, live (fixes66): collected nodes answer no `_graph`, and a user's own `_graph` data is kept.

The expected answers are written by hand: the maps and strings are the ones each test writes.
"""

from __future__ import annotations

import json
import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

NODE_TEXT = '{"_id":"z","_labels":["L"],"_props":[],"_graph":"g"}'


@pytest.fixture()
def eng(iris_connection):
    e = IRISGraphEngine(iris_connection)
    e.execute_cypher("MATCH (n:NG310) DETACH DELETE n", raise_on_error=True)
    # a map is stored through the API (a Cypher SET of a map is InvalidPropertyType)
    assert e.create_node("ng310:1", labels=["NG310"], properties={"k": "v", "s": NODE_TEXT, "m": {"a": 1, "_graph": "x"}})
    try:
        yield e
    finally:
        e.execute_cypher("MATCH (n:NG310) DETACH DELETE n")


def _one(e, q, p=None):
    r = e.execute_cypher(q, p or {}, raise_on_error=True)
    assert not r.error, r.error
    [[v]] = r.rows
    return v


def _json(v):
    return json.loads(v) if isinstance(v, str) else v


@pytest.mark.parametrize("q", [
    "MATCH (n:NG310) RETURN collect(n) AS c",
    "MATCH (n:NG310) RETURN collect(n)[0] AS c",
    "MATCH (n:NG310) WITH collect(n) AS ns UNWIND ns AS m RETURN m",
])
def test_a_collected_node_answers_no_graph(eng, q):
    v = _json(_one(eng, q))
    nodes = [_json(x) for x in v] if isinstance(v, list) else [v]
    assert [set(n) for n in nodes] == [{"_id", "_labels", "_props"}]  # its properties hold `_graph`: those stay


def test_a_map_literal_with_a_graph_key(eng):
    assert _json(_one(eng, "RETURN {a: 1, _graph: 'x'} AS m")) == {"a": 1, "_graph": "x"}


def test_a_map_parameter_with_a_graph_key(eng):
    assert _json(_one(eng, "RETURN $m AS m", {"m": {"a": 1, "_graph": "x"}})) == {"a": 1, "_graph": "x"}


def test_a_stored_map_with_a_graph_key(eng):
    assert _json(_one(eng, "MATCH (n:NG310) RETURN n.m AS m")) == {"a": 1, "_graph": "x"}


def test_a_string_that_spells_a_node_with_a_graph(eng):
    # a string that spells a map reads as the map (KNOWN_ISSUES): what is pinned is that its `_graph` stays
    want = json.loads(NODE_TEXT)
    assert _json(_one(eng, "MATCH (n:NG310) RETURN n.s AS s")) == want
    got = _json(_one(eng, "MATCH (n:NG310) RETURN collect(n.s) AS c"))
    assert [_json(x) for x in got] == [want]


def test_a_collected_node_keeps_its_properties_that_spell_a_node(eng):
    m = _json(_one(eng, "MATCH (n:NG310) RETURN collect(n)[0] AS c"))
    props = {p["key"]: p["value"] for p in (json.loads(x) for x in m["_props"])}
    assert set(m) == {"_id", "_labels", "_props"} and props["s"] == json.loads(NODE_TEXT)
    assert props["m"] == {"a": 1, "_graph": "x"}
