"""Variable-length expansion that cannot observe individual paths uses CY_VLP_ENDS.

`SQLUser.CY_VLP_PATHS` returns one JSON string holding every edge-distinct trail, so a
three-hop `*1..3` on a social graph overflows IRIS's string limit (<MAXSTRING>, SQLCODE
-149) although the query only wants the distinct people at the far end. When the query
reads nothing but the target node and returns DISTINCT rows, the translator calls
`SQLUser.CY_VLP_ENDS`, which returns each reachable end once with its minimum depth.
Every other shape keeps CY_VLP_PATHS.
"""

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.schema import GraphSchema

IC1 = (
    "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId AND "
    "f.firstName = $firstName AND f.sid <> $personId "
    "RETURN DISTINCT f.sid, f.lastName ORDER BY f.sid LIMIT 20"
)
PARAMS = {"personId": "14", "firstName": "Ken"}


def tr(q, params=None):
    return translate_to_sql(parse_query(q), params if params is not None else PARAMS)


def sql_of(q, params=None):
    t = tr(q, params)
    return t.sql if isinstance(t.sql, str) else "\n".join(t.sql)


def test_eligible_shape_uses_ends_udf():
    sql = sql_of(IC1)
    # (the table name carries the schema prefix other tests may have set)
    assert re.search(
        r"SQLUser\.CY_VLP_ENDS\(n0\.node_id, '\[\"KNOWS\"\]', 'both', 1, 3, 1, '-', '(\w+\.)?rdf_edges'\)", sql
    ), sql
    assert "CY_VLP_PATHS" not in sql
    # the columns nothing can read are not produced
    assert "PATH '$.k'" not in sql and "PATH '$.n'" not in sql


@pytest.mark.parametrize(
    "q",
    [
        # no DISTINCT: one row per path is observable
        "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId RETURN f.sid",
        # named relationship
        "MATCH (p:Person)-[r:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId RETURN DISTINCT f.sid",
        # the source is returned
        "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId RETURN DISTINCT p.sid, f.sid",
        "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId RETURN DISTINCT f.sid, p.firstName",
        # named path
        "MATCH path = (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId RETURN DISTINCT f.sid",
        # relationship properties
        "MATCH (p:Person)-[:KNOWS*1..3 {w: 1}]-(f:Person) WHERE p.sid = $personId RETURN DISTINCT f.sid",
        # an aggregate counts paths, DISTINCT or not
        "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId RETURN DISTINCT count(f)",
        "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId RETURN count(DISTINCT f)",
        # a second hop in the pattern
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person)-[:KNOWS]-(g:Person) WHERE p.sid = $personId RETURN DISTINCT g.sid",
        # a second MATCH
        "MATCH (p:Person) WHERE p.sid = $personId MATCH (p)-[:KNOWS*1..3]-(f:Person) RETURN DISTINCT f.sid",
        # OPTIONAL MATCH
        "MATCH (p:Person) WHERE p.sid = $personId OPTIONAL MATCH (p)-[:KNOWS*1..3]-(f:Person) RETURN DISTINCT f.sid",
        # a WITH between the pattern and the result
        "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId WITH f RETURN DISTINCT f.sid",
        # RETURN item that is a whole-pattern value
        "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId RETURN DISTINCT f.sid, p",
    ],
)
def test_ineligible_shapes_keep_paths_udf(q):
    sql = sql_of(q)
    assert "CY_VLP_ENDS" not in sql, sql
    assert "CY_VLP_PATHS" in sql or "CY_VLP_OPT" in sql, sql


@pytest.mark.parametrize(
    "q",
    [
        IC1,
        "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId RETURN f.sid",
        "MATCH (p:Person)-[:KNOWS*2..3]->(f:Person) WHERE p.sid = $personId RETURN DISTINCT f.sid",
    ],
)
def test_parameters_match_placeholders(q):
    t = tr(q)
    sql = t.sql if isinstance(t.sql, str) else t.sql[0]
    assert sql.count("?") == len(t.parameters[0]), (sql, t.parameters)


def test_ends_variant_keeps_bounds_and_direction():
    sql = sql_of(
        "MATCH (p:Person)-[:KNOWS*2..3]->(f:Person) WHERE p.sid = $personId RETURN DISTINCT f.sid"
    )
    assert re.search(
        r"CY_VLP_ENDS\(n0\.node_id, '\[\"KNOWS\"\]', 'out', 2, 3, 1, '-', '(\w+\.)?rdf_edges'\)", sql
    ), sql


def test_seed_predicate_stays_where_it_was():
    """The seed `p.sid = $id` is left to the optimiser: on IRIS 2026.3 the UDF ran once for
    the seed whether the source's property join sat before or after the expansion (measured
    with a call counter in SNBBENCHB), so the join order is not rewritten."""
    t = tr(IC1)
    assert t.parameters[0] == [
        "Person", "Person", "sid", "firstName", "sid", "sid", "lastName", "sid", "14", "Ken", "14",
    ], t.parameters
    assert t.sql.index("CY_VLP_ENDS") < t.sql.index("rdf_props p5")


class TestDdl:
    @staticmethod
    def _ddl():
        return next(
            s for s in GraphSchema.get_procedures_sql_list() if "FUNCTION SQLUser.CY_VLP_ENDS(" in s
        )

    def test_registered_with_the_paths_signature(self):
        ddl = self._ddl()
        paths = next(
            s for s in GraphSchema.get_procedures_sql_list() if "FUNCTION SQLUser.CY_VLP_PATHS(" in s
        )
        sig = lambda s: s.split("(", 1)[1].split(") RETURNS", 1)[0]
        assert sig(ddl) == sig(paths)
        assert ddl.lstrip().startswith("CREATE OR REPLACE FUNCTION")

    def test_locals_are_private_and_body_survives_dbapi_ddl(self):
        ddl = self._ddl()
        body = ddl.split("LANGUAGE OBJECTSCRIPT {", 1)[1].lstrip()
        assert body.startswith("New ")
        newed = {v.strip() for v in body[4:].split(" Set ", 1)[0].split(",")}
        assert {"adj", "st", "sd", "se", "args", "sql", "targ", "best", "ord"} <= newed
        # an apostrophe or question mark in a LANGUAGE OBJECTSCRIPT body breaks DBAPI DDL
        assert "'" not in body and "?" not in body

    def test_returns_only_end_and_min_depth(self):
        body = self._ddl().split("LANGUAGE OBJECTSCRIPT {", 1)[1]
        assert '"t"' in body and '"l"' in body
        for key in ('"n"', '"y"', '"r"', '"k"'):
            assert key not in body
