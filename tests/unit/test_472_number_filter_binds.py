"""4.7.2: a number filter on a node property binds as many values as the SQL has markers.

The stored-number test of 4.7.0 row 24 (and its 4.7.2 extension to carried, unwound and OPTIONAL MATCH nodes) names the
property's value more than once in the statement. Where that value is a subquery with a `"key" = ?` marker, every copy
carries its own marker, and the parameters were added once: TCK MatchWhere6 [3], `MATCH (n:Single) OPTIONAL MATCH
(n)-[r]-(m) WHERE m.num = 42 RETURN m`, had 4 markers for 2 parameters and IRIS answered <ARGUMENT ERROR> Incorrect
number of parameters. Each shape is translated and its markers counted (outside quoted text) against its parameters.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import _blank_sql_literals, translate_to_sql

FILTERS = ["= 42", "<> 42", "> 42", ">= 42", "< 42", "<= 42", "= 0.95", ">= 0.95", "= $v", "> $v"]

SHAPES = {
    "match-where": "MATCH (m:N) WHERE m.num {f} RETURN m",
    "optional-where": "MATCH (n:Single) OPTIONAL MATCH (n)-[r]-(m) WHERE m.num {f} RETURN m",
    "optional-where-labelled": "MATCH (n:Single) OPTIONAL MATCH (n)-[r]-(m:N) WHERE m.num {f} RETURN m",
    "optional-where-and": "MATCH (n:Single) OPTIONAL MATCH (n)-[r]-(m) WHERE m.num {f} AND m.id <> 'x' RETURN m.id",
    "with-carried": "MATCH (m:N) WITH m WHERE m.num {f} RETURN m.id",
    "with-alias": "MATCH (m:N) WITH m.num AS v, m WHERE m.num {f} RETURN m.id",
    "unwound-collect": "MATCH (n:N) WITH collect(n) AS ns UNWIND ns AS m WITH m WHERE m.num {f} RETURN m.id",
    "comprehension": "MATCH (n:N) WITH collect(n) AS ns RETURN [m IN ns WHERE m.num {f} | m.id]",
    "path-comprehension": "MATCH p = (:N {{id: 'n1'}})-[:R*4]->() RETURN [m IN nodes(p) WHERE m.num {f} | m.id]",
    "path-predicate": "MATCH p = (:N {{id: 'n2'}})-[:R*1..2]->() WHERE any(m IN nodes(p) WHERE m.num {f}) RETURN count(p)",
    "optional-with": "MATCH (n:Single) OPTIONAL MATCH (n)-[r]-(m) WITH n, m WHERE m.num {f} RETURN m",
    "match-pattern-prop": "MATCH (m:N {{num: 42}}) OPTIONAL MATCH (m)-[r]-(o) WHERE o.num {f} RETURN o",
}


def _markers(sql: str) -> int:
    return _blank_sql_literals(sql).count("?")


def _statements(cypher: str, params: dict):
    """[(sql, parameters)] of each statement: `.sql` is a string for one, a list for several, and `.parameters` is a
    list of parameter lists, one per statement."""
    out = translate_to_sql(parse_query(cypher), dict(params))
    sqls = [out.sql] if isinstance(out.sql, str) else list(out.sql)
    plists = list(out.parameters)
    assert len(sqls) == len(plists), (sqls, plists)
    return list(zip(sqls, plists))


# 4.7.1 already binds `> $v` once where the SQL names it three times (the NaN decomposition of a range, repeated by
# `any` and by a list comprehension over `nodes(p)`), whatever the property: measured on 6768c109 with `m.id > $v` too.
# Not the number rule's; strict, so the case turns red the day it is fixed.
PRE_EXISTING = {("path-comprehension", "> $v"), ("path-predicate", "> $v")}


def _case(shape, flt):
    marks = [pytest.mark.xfail(strict=True, reason="range over a $parameter binds once for several markers (4.7.1)")]
    return pytest.param(shape, flt, marks=marks if (shape, flt) in PRE_EXISTING else [])


@pytest.mark.parametrize("shape,flt", [_case(s, f) for s in SHAPES for f in FILTERS])
def test_every_marker_has_a_parameter(shape, flt):
    cypher = SHAPES[shape].format(f=flt)
    for sql, params in _statements(cypher, {"v": 42}):
        if not isinstance(sql, str):
            continue
        assert _markers(sql) == len(params), (cypher, _markers(sql), params, sql)


def test_the_tck_scenario_pairs_markers_and_values():
    """MatchWhere6 [3]: the label is bound, the property key is inlined in each copy of the read, and every marker has
    its value (4.7.1 had 2 and 2; 4.7.2 before this fix had 4 and 2)."""
    [(sql, params)] = _statements("MATCH (n:Single) OPTIONAL MATCH (n)-[r]-(m) WHERE m.num = 42 RETURN m", {})
    assert list(params) == ["Single"], params
    assert _markers(sql) == 1, sql
    assert "\"key\" = 'num'" in sql, sql
