"""4.6.0 R305, offline: a CREATE or MERGE between matched endpoints writes a list- or map-valued relationship
property (and every other property beside it), and `SET r.k = <list>` stores a JSON list.

On the release head (18983c4d) `MATCH (a), (b) CREATE (a)-[:T {l: [1, 2], w: 7}]->(b)` wrote the relationship with
no qualifiers at all (w lost too): _rel_literal_props answered None for a list, a map, or an expression, and the
writer then wrote none. `SET r.m = [3, 4]` stored the JSON string "[3, 4]". The live reads are in
tests/e2e/test_460_r305_rel_props_writes_e2e.py.
"""

from __future__ import annotations

import json

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

MATCHED = "MATCH (a:Z {name: 'a'}), (b:Z {name: 'b'}) "


def _params(q, params=None):
    r = translate_to_sql(parse_query(q), params or {})
    return [p for ps in r.parameters for p in ps]


def _json_params(q, params=None):
    out = []
    for p in _params(q, params):
        if isinstance(p, str) and p.startswith("{"):
            out.append(json.loads(p))
    return out


@pytest.mark.parametrize("verb", ["CREATE", "MERGE"])
def test_a_list_or_map_property_between_matched_endpoints_is_written(verb):
    got = _json_params(MATCHED + verb + " (a)-[:T {l: [1, 2], m: {k: 'x'}, w: 7}]->(b)")
    # a number keeps the text a Cypher write stores ("7")
    assert {"l": [1, 2], "m": {"k": "x"}, "w": "7"} in got, got


def test_a_parameter_list_between_matched_endpoints_is_written():
    got = _json_params(MATCHED + "CREATE (a)-[:T {l: $l, w: 7}]->(b)", {"l": [1, 2]})
    assert {"l": [1, 2], "w": "7"} in got, got


class _MultigraphEngine:
    """Only what the translator asks of an engine about multigraph mode (the TCK's default graph runs in it)."""

    def is_multigraph(self, graph):
        return True

    def get_table_mapping(self, label):
        return None

    def get_rel_mapping(self, *a):
        return None


def test_a_multigraph_merge_matches_a_list_by_its_stored_json():
    # with multigraph mode off a MERGE matches the triple alone; on, it matches the inline properties too (spec 234),
    # and JSON_VALUE answers a stored list as '[1,2]'
    r = translate_to_sql(parse_query(MATCHED + "MERGE (a)-[:T {l: [1, 2]}]->(b)"), {}, engine=_MultigraphEngine())
    params = [p for ps in r.parameters for p in ps]
    assert "[1,2]" in params, params


@pytest.mark.parametrize(
    "q, params",
    [
        ("MATCH ()-[r:T]->() SET r.m = [3, 4]", None),
        ("MATCH ()-[r:T]->() SET r.m = $v", {"v": [3, 4]}),
        ("MATCH ()-[r:T]->() SET r += {m: [3, 4]}", None),
    ],
)
def test_a_set_of_a_list_stores_json(q, params):
    p = _params(q, params)
    i = p.index("m")
    assert p[i + 1 : i + 3] == ["[3, 4]", "json"], p


def test_a_set_of_a_string_is_still_a_string():
    p = _params("MATCH ()-[r:T]->() SET r.m = '[3, 4]'")
    i = p.index("m")
    assert p[i + 1 : i + 3] == ["[3, 4]", "string"], p
