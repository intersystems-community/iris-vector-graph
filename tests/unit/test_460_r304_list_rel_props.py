"""4.6.0 R304, offline: a Cypher CREATE or MERGE of a relationship with a list-valued property stores the list as
JSON, as create_edge stores it; a number keeps its stored text.

On the release head (6f9e732f) `CREATE (a)-[:T {l: [1, 2]}]->(b)` with endpoints the statement creates, and the
equivalent MERGE, stored the qualifiers `{"l": "[Literal(value=1), Literal(value=2)]"}`: the parse tree's text
(found by fixes50, 2026-11-05). The live reads are in tests/e2e/test_460_r304_list_rel_props_e2e.py.
"""

from __future__ import annotations

import json

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _qualifiers(q):
    r = translate_to_sql(parse_query(q), {})
    out = []
    for params in r.parameters:
        for p in params:
            if isinstance(p, str) and p.startswith("{"):
                out.append(json.loads(p))
    return out


@pytest.mark.parametrize(
    "q",
    [
        "CREATE (a:Z)-[:T {l: [1, 2], s: ['a', 'b'], w: 7}]->(b:Z)",
        "CREATE (:Z {id: 'x1'})-[:T {l: [1, 2], s: ['a', 'b'], w: 7}]->(:Z {id: 'x2'})",
        "MERGE (a:Z {id: 'x1'})-[:T {l: [1, 2], s: ['a', 'b'], w: 7}]->(b:Z {id: 'x2'})",
    ],
)
def test_a_list_property_is_stored_as_a_json_list(q):
    got = _qualifiers(q)
    # the number keeps its stored text '7' (the hard rule: no writer changes how numbers are stored)
    assert {"l": [1, 2], "s": ["a", "b"], "w": "7"} in got, got
    assert not any("Literal(" in json.dumps(g) for g in got), got
