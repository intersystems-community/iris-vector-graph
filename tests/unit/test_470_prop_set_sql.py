"""4.7.0 row 35: every UPDATE of rdf_props.val a Cypher write makes sets it through Prop_Set (Graph.KG.PropWrite).

IRIS's filer writes no value whose text equals the stored one, so a write of 30 over the string '30' left the
string; Prop_Set writes such a value into the row (the answers are in tests/e2e/test_470_prop_retype_e2e.py). One
UPDATE that sets val another way would leave that write path with the old defect, so each write shape is checked here.
A namespace whose classes predate 4.7.0 has no Prop_Set: for an engine that says so the UPDATE sets the value as
4.6.x did, rather than fail every write with SQLCODE -359.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

SHAPES = [
    ("MATCH (n:P) SET n.p = 30", {}),
    ("MATCH (n:P) SET n.p = $v", {"v": 30}),
    ("MATCH (n:P) SET n.p = n.q + 1", {}),
    ("MATCH (n:P) SET n.p = toInteger(n.p)", {}),
    ("MATCH (n:P) SET n.p = toFloat(n.p), n.s = toString(n.i), n.t = toBoolean(n.t)", {}),
    ("MATCH (n:P) SET n += {p: 30}", {}),
    ("MATCH (n:P) WITH n, toInteger(n.p) AS v SET n.p = v", {}),
    ("MATCH (n:P), (m:Q) SET n.p = m.q", {}),
    ("MERGE (n:P {id: 'a'}) ON MATCH SET n.p = 30", {}),
    ("MERGE (n:P {id: 'a'}) ON CREATE SET n.p = 30", {}),
    ("MATCH (n:P {id: 'a'}) MERGE (n)-[:R]->(m:Q {id: 'b'}) ON MATCH SET m.p = 30", {}),
]

_UPDATE_VAL = re.compile(r"UPDATE \S*rdf_props SET val = ")


def _updates(q, params, engine=None):
    sql = translate_to_sql(parse_query(q), dict(params), engine=engine).sql
    return [s for s in (sql if isinstance(sql, list) else [sql]) if isinstance(s, str) and _UPDATE_VAL.search(s)]


@pytest.mark.parametrize("q, params", SHAPES)
def test_every_property_update_goes_through_prop_set(q, params):
    for s in _updates(q, params):
        assert "Graph_KG.Prop_Set(%ID, val, " in s, s


def test_the_shapes_do_update_a_property():
    assert all(_updates(q, p) for q, p in SHAPES[:5])


@pytest.mark.parametrize("q, params", SHAPES)
def test_without_prop_set_the_value_is_set_as_before(q, params):
    old = SimpleNamespace(_schema_prefix="Graph_KG", _prop_set_deployed=False, get_table_mapping=lambda *a, **k: None)
    for s in _updates(q, params, engine=old):
        assert "Prop_Set" not in s, s
