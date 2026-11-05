"""4.6.0 R182: `toString()` of a stored property answers openCypher's text for a number, and a string unchanged.

`toString(x)` was `CAST(x AS VARCHAR)`, which spells a number the way IRIS does: `.95` for a relationship's JSON
number (`create_edge`, `SET r.p = 0.95`), `.94999999999999995559` for a node's `$DOUBLE` (a Cypher CREATE or SET), `1`
for a float 1.0 a Cypher CREATE wrote on a node. openCypher answers `'0.95'`, `'1.0'` and `'7'`.

The read side decides from what is stored, and changes nothing that is stored:
- a node property goes through `CY_TOSTRING(val)`, which reads the value's own type in the row (`$LISTBUILD`): a
  `$DOUBLE` is spelled as the shortest decimal that reads back as it, in openCypher's float form; an integer as its
  digits; a string (every API-written value, `'0.95'` and `'.95'` and `'007'` alike) as it is;
- a relationship property goes through `CY_REL_TOSTRING(qualifiers, '$.p')`, which reads the JSON leaf: a number by
  its JSON text (`1.0` is a float, `7` an integer), a string as it is.

The function bodies are checked live (tests/e2e/test_460_tostring_stored_number_e2e.py); these pin which reads take
them, and that every other `toString` is the CAST it was.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _sql(q, params=None):
    r = translate_to_sql(parse_query(q), params or {})
    return r.sql if isinstance(r.sql, str) else r.sql[-1]


def test_node_property_reads_through_cy_tostring():
    sql = _sql("MATCH (n:M) RETURN toString(n.score) AS s")
    # the property's own column (or its correlated subquery), not a CAST of it
    assert re.search(r"CY_TOSTRING\((%EXACT\(p\d+\.val\)|\(SELECT val FROM [^)]*\))\) AS s", sql), sql
    assert "CAST(" not in sql.split(" AS s", 1)[0], sql


def test_relationship_property_reads_through_cy_rel_tostring():
    sql = _sql("MATCH ()-[r:S]->() RETURN toString(r.score) AS s")
    assert re.search(r"CY_REL_TOSTRING\(e\d+\.qualifiers, '\$\.score'\)", sql), sql
    assert "JSON_VALUE" not in sql, sql


def test_in_a_where_clause_too():
    sql = _sql("MATCH (n:M) WHERE toString(n.score) = '0.95' RETURN n")
    assert "CY_TOSTRING(" in sql, sql
    sql = _sql("MATCH ()-[r:S]->() WHERE toString(r.score) = '0.95' RETURN r")
    assert "CY_REL_TOSTRING(" in sql, sql


def test_with_column_of_a_node_property():
    sql = _sql("MATCH (n:M) WITH n.score AS x RETURN toString(x) AS s")
    assert re.search(r"CY_TOSTRING\(Stage1\.x\) AS s", sql), sql


@pytest.mark.parametrize(
    "q",
    [
        "RETURN toString(0.95) AS s",
        # a concatenation (4.6.0 R292: `n.score + 1` is arithmetic, spelled by CY_TOSTRING as `n.score - 1` is)
        "MATCH (n:M) RETURN toString(n.score + 'a') AS s",
        "UNWIND [1, 2] AS x RETURN toString(x) AS s",
    ],
)
def test_other_arguments_are_the_cast_they_were(q):
    sql = _sql(q)
    assert "CY_TOSTRING" not in sql and "CY_REL_TOSTRING" not in sql, sql


def test_the_functions_are_installed_with_the_schema():
    from iris_vector_graph.schema import GraphSchema

    ddl = "\n".join(GraphSchema.get_procedures_sql_list("Graph_KG"))
    for name in ("CY_TOSTRING", "CY_REL_TOSTRING", "CY_FLOAT_TEXT"):
        assert f"CREATE OR REPLACE FUNCTION Graph_KG.{name}(" in ddl, name
    body = ddl.split("FUNCTION Graph_KG.CY_TOSTRING(", 1)[1].split("\n", 1)[0]
    # the value's own type in the row: a $DOUBLE is $LISTBUILD type 8 or 9 (probed on 2026.3.0AI.113.0)
    assert "$ASCII($LISTBUILD(v), 2)" in body and "(t = 8) || (t = 9)" in body, body
    # DBAPI DDL tokenizing: no apostrophe, question mark or `:1` in an ObjectScript body (iris_ddl_objectscript)
    for name in ("CY_TOSTRING", "CY_REL_TOSTRING", "CY_FLOAT_TEXT"):
        b = ddl.split(f"FUNCTION Graph_KG.{name}(", 1)[1].split("\n", 1)[0]
        assert "'" not in b.split("LANGUAGE OBJECTSCRIPT", 1)[1] and "?" not in b and ":1" not in b, name
