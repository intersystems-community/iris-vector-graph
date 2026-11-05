"""4.6.0 R305 follow-up, offline: a CREATE or MERGE between matched endpoints whose relationship property is computed
from the row (`{w: a.id}`) raises GraphWriteError while translating, before anything is written.

On the release head (and 4.5.1) such a write created the relationship with none of its properties: the writer stores
literals, parameters and a WITH's columns only. Storing a row's value there is a 4.6.1 item (docs/KNOWN_ISSUES.md);
until then the write is refused rather than losing the data. Live: tests/e2e/test_460_r305_refuse_row_props_e2e.py.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.errors import GraphWriteError

MATCHED = "MATCH (a:Z {name: 'a'}), (b:Z {name: 'b'}) "


class _MultigraphEngine:
    def is_multigraph(self, graph):
        return True

    def get_table_mapping(self, label):
        return None

    def get_rel_mapping(self, *a):
        return None


@pytest.mark.parametrize("engine", [None, _MultigraphEngine()])
@pytest.mark.parametrize(
    "tail",
    [
        "CREATE (a)-[:T {w: a.name, k: 1}]->(b)",
        "MERGE (a)-[:T {w: a.name}]->(b)",
        "CREATE (a)-[:T {l: [a.name, 1]}]->(b)",
        "CREATE (a)-[:T {w: 1 + 2}]->(b)",
    ],
)
def test_a_row_computed_property_is_refused(tail, engine):
    with pytest.raises(GraphWriteError, match=r"property `(w|l)`.*KNOWN_ISSUES"):
        translate_to_sql(parse_query(MATCHED + tail), {}, engine=engine)


@pytest.mark.parametrize(
    "q, params",
    [
        (MATCHED + "CREATE (a)-[:T {w: 7, l: [1, 2], m: {k: 'x'}}]->(b)", None),
        (MATCHED + "CREATE (a)-[:T {w: $p}]->(b)", {"p": 5}),
        (MATCHED + "WITH a, b, split('x,y', ',') AS roles MERGE (a)-[:T {r: roles}]->(b)", None),
        ("CREATE (a:Z)-[:T {w: 7}]->(b:Z)", None),
    ],
)
def test_a_storable_property_is_not_refused(q, params):
    translate_to_sql(parse_query(q), params or {})
