"""Variable-length expansion that cannot observe individual paths uses CY_VLP_ENDS.

`SQLUser.CY_VLP_PATHS` returns one JSON string holding every edge-distinct trail, so a
three-hop `*1..3` on a social graph overflows IRIS's string limit (<MAXSTRING>, SQLCODE
-149) although the query only wants the distinct people at the far end. When the query
cannot observe how many trails reach a target (or the trail itself), the translator
calls `SQLUser.CY_VLP_ENDS`, which returns each reachable end once with its minimum depth.
Every other shape keeps CY_VLP_PATHS. `_vlp_ends_target` documents the rule.
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
        # a second hop of the same type: the trail's edges must not be reused (CY_VLP_HAS reads them)
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person)-[:KNOWS]-(g:Person) WHERE p.sid = $personId RETURN DISTINCT g.sid",
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


# --- the widened rule: nothing can observe how many trails reach a target ----------

IC3 = (
    "MATCH (p:Person)-[:KNOWS*2..2]-(f:Person) WHERE p.sid = $personId AND f.sid <> $personId "
    "RETURN count(DISTINCT f)"
)
IC5 = (
    "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person)<-[:HAS_MEMBER]-(forum:Forum) "
    "WHERE p.sid = $personId AND f.sid <> $personId "
    "RETURN forum.title AS title, count(DISTINCT f) AS c ORDER BY c DESC, title LIMIT 20"
)
IC6 = (
    "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person)<-[:HAS_CREATOR]-(m:Post)-[:HAS_TAG]->(t:Tag), "
    "(m)-[:HAS_TAG]->(o:Tag) WHERE p.sid = $personId AND t.name = $tagName AND o.name <> $tagName "
    "AND f.sid <> $personId RETURN o.name AS name, count(DISTINCT m) AS c ORDER BY c DESC, name"
)
IC9 = (
    "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person)<-[:HAS_CREATOR]-(m:Post) WHERE p.sid = $personId "
    "AND m.creationDate < $maxDate AND f.sid <> $personId "
    "RETURN DISTINCT f.sid, m.sid, m.creationDate ORDER BY m.creationDate DESC, m.sid LIMIT 20"
)
P2 = {"personId": "14", "tagName": "Elvis", "maxDate": "2012-01-01"}

ELIGIBLE = [
    pytest.param(IC3, id="IC3-count-distinct"),
    pytest.param(IC5, id="IC5-forums"),
    pytest.param(IC6, id="IC6-two-patterns"),
    pytest.param(IC9, id="IC9-distinct-after-hop"),
    pytest.param(
        "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId RETURN collect(DISTINCT f.sid)",
        id="collect-distinct",
    ),
    pytest.param(
        "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId RETURN max(f.sid), min(f.sid)",
        id="min-max",
    ),
    pytest.param(
        "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId "
        "RETURN f.lastName, count(DISTINCT f) AS c ORDER BY c DESC",
        id="group-key-with-distinct-count",
    ),
    pytest.param(
        "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId RETURN count(DISTINCT f) + 1 AS c",
        id="aggregate-inside-arithmetic",
    ),
    pytest.param(
        "MATCH (p:Person) WHERE p.sid = $personId MATCH (p)-[:KNOWS*1..3]-(f:Person) RETURN DISTINCT f.sid",
        id="second-match",
    ),
    pytest.param(
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person)<-[:HAS_CREATOR]-(m:Post) WHERE p.sid = $personId "
        "MATCH (m)-[:HAS_TAG]->(t:Tag) RETURN DISTINCT t.name",
        id="hop-then-second-match",
    ),
    pytest.param(
        "MATCH (a:Forum)-[:HAS_MEMBER]->(p:Person)-[:KNOWS*1..2]-(f:Person) WHERE a.sid = $personId "
        "RETURN DISTINCT f.sid",
        id="hop-before-the-expansion",
    ),
]


@pytest.mark.parametrize("q", ELIGIBLE)
def test_widened_rule_uses_ends_udf(q):
    sql = sql_of(q, P2)
    assert "CY_VLP_ENDS" in sql, sql
    assert "CY_VLP_PATHS" not in sql, sql
    # the exclusion conditions read the edge identities, which the ends form does not produce
    assert "CY_VLP_HAS" not in sql and "CY_VLP_DISJOINT" not in sql, sql
    assert "PATH '$.k'" not in sql and "PATH '$.r'" not in sql


def test_eligible_queries_bind_every_placeholder():
    for q in (IC3, IC5, IC6, IC9):
        t = tr(q, P2)
        sql = t.sql if isinstance(t.sql, str) else t.sql[0]
        assert sql.count("?") == len(t.parameters[0]), (q, sql, t.parameters)


def test_bench_queries_are_the_eligible_ones():
    """The four SNB queries that overflowed at SF1 are the eligible ones, in the text IVG runs."""
    from scripts.bench.alt import snb_queries

    for name, text in (
        ("IC3_foaf_count", IC3),
        ("IC5_forums_of_friends", IC5),
        ("IC6_tag_cooccurrence", IC6),
        ("IC9_recent_in_network", IC9),
    ):
        ran = snb_queries.ivg_cypher(snb_queries.QUERIES[name])
        assert "CY_VLP_ENDS" in sql_of(ran, P2), name


@pytest.mark.parametrize(
    "q",
    [
        # an aggregate that counts trails
        "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId RETURN count(f)",
        "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId RETURN count(*)",
        "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId RETURN f.firstName, count(f) AS c",
        "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId RETURN sum(f.age)",
        "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId RETURN avg(f.age)",
        "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId RETURN collect(f.sid)",
        "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId RETURN stDev(f.age)",
        # one sensitive aggregate spoils a row of safe ones
        "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId "
        "RETURN count(DISTINCT f) AS a, count(f) AS b",
        # a sensitive aggregate only in ORDER BY
        "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId "
        "RETURN f.lastName, count(DISTINCT f) AS a ORDER BY count(f)",
        # plain rows with no DISTINCT: one row per trail
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person)<-[:HAS_CREATOR]-(m:Post) WHERE p.sid = $personId "
        "RETURN f.sid, m.sid ORDER BY m.sid LIMIT 20",
        # the relationship, or a path holding it
        "MATCH (p:Person)-[r:KNOWS*1..2]-(f:Person) WHERE p.sid = $personId RETURN count(DISTINCT f)",
        "MATCH path = (p:Person)-[:KNOWS*1..2]-(f:Person) WHERE p.sid = $personId RETURN DISTINCT length(path)",
        "MATCH path = (p:Person)-[:KNOWS*1..2]-(f:Person) WHERE p.sid = $personId RETURN count(DISTINCT f)",
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person)<-[:HAS_CREATOR]-(m:Post) WHERE p.sid = $personId "
        "MATCH path = (m)-[:HAS_TAG]->(t:Tag) RETURN DISTINCT t.name",
        # relationship properties filter every relationship of the trail
        "MATCH (p:Person)-[:KNOWS*1..2 {w: 1}]-(f:Person) WHERE p.sid = $personId RETURN count(DISTINCT f)",
        # OPTIONAL MATCH, anywhere
        "MATCH (p:Person) WHERE p.sid = $personId OPTIONAL MATCH (p)-[:KNOWS*1..2]-(f:Person) "
        "RETURN count(DISTINCT f)",
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person) WHERE p.sid = $personId "
        "OPTIONAL MATCH (f)<-[:HAS_CREATOR]-(m:Post) RETURN DISTINCT f.sid, m.sid",
        # WITH that re-aggregates, or any WITH
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person) WHERE p.sid = $personId "
        "WITH f.lastName AS n, count(f) AS c RETURN DISTINCT n, c",
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person) WHERE p.sid = $personId "
        "WITH DISTINCT f RETURN count(DISTINCT f)",
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person) WHERE p.sid = $personId "
        "WITH f, count(f) AS c WHERE c > 1 RETURN DISTINCT f.sid",
        # a clause that acts once per row
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person) WHERE p.sid = $personId SET f.seen = 1 "
        "RETURN count(DISTINCT f)",
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person) WHERE p.sid = $personId UNWIND [1, 2] AS x "
        "RETURN DISTINCT f.sid, x",
        # the other hop could use the trail's edges: same type, or any type
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person)-[:KNOWS]-(g:Person) WHERE p.sid = $personId "
        "RETURN count(DISTINCT g)",
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person)-[]-(g) WHERE p.sid = $personId RETURN DISTINCT g.sid",
        "MATCH (p:Person)-[:KNOWS|HAS_CREATOR*1..2]-(f)<-[:HAS_CREATOR]-(m:Post) WHERE p.sid = $personId "
        "RETURN DISTINCT m.sid",
        "MATCH (p:Person)-[*1..2]-(f)<-[:HAS_CREATOR]-(m:Post) WHERE p.sid = $personId RETURN DISTINCT m.sid",
        # an untyped expansion alone is fine on its own, but a second expansion is not
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person)-[:HAS_MEMBER*1..2]-(g) WHERE p.sid = $personId "
        "RETURN DISTINCT g.sid",
        # shortest paths keep the engine route
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person) WHERE p.sid = $personId RETURN DISTINCT f.sid, "
        "shortestPath((p)-[:KNOWS*..3]-(f))",
        # no target variable, or the source is the target
        "MATCH (p:Person)-[:KNOWS*1..2]-(:Person) WHERE p.sid = $personId RETURN count(DISTINCT p)",
        "MATCH (p:Person)-[:KNOWS*1..2]-(p) RETURN DISTINCT p.sid",
        # the source is read by RETURN or ORDER BY (sound, but left on the trail form)
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person) WHERE p.sid = $personId "
        "RETURN p.sid AS s, count(DISTINCT f) AS c",
    ],
)
def test_widened_rule_still_refuses(q):
    sql = sql_of(q, P2)
    assert "CY_VLP_ENDS" not in sql, sql


def test_trail_form_keeps_its_exclusion_conditions():
    """A second hop of the same type stays on CY_VLP_PATHS, which still keeps its edges off the trail."""
    sql = sql_of(
        "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person)-[:KNOWS]-(g:Person) WHERE p.sid = $personId "
        "RETURN DISTINCT g.sid",
        P2,
    )
    assert "CY_VLP_PATHS" in sql and "CY_VLP_HAS" in sql, sql


class TestEndsOnlyRule:
    """`_vlp_ends_target` directly: the target variable, or None."""

    @staticmethod
    def rule(q):
        from iris_vector_graph.cypher.translator import _vlp_ends_target

        return _vlp_ends_target(parse_query(q))

    def test_names_the_target(self):
        assert self.rule(IC3) == "f"
        assert self.rule(IC5) == "f"
        assert self.rule(IC9) == "f"

    def test_distinct_required_without_an_aggregate(self):
        q = "MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) RETURN f.sid"
        assert self.rule(q) is None
        assert self.rule(q.replace("RETURN", "RETURN DISTINCT")) == "f"

    def test_distinct_keyword_does_not_excuse_a_sensitive_aggregate(self):
        assert self.rule("MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) RETURN DISTINCT count(f)") is None

    def test_aggregate_without_distinct_keyword_is_enough_when_insensitive(self):
        assert self.rule("MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) RETURN count(DISTINCT f)") == "f"
        assert self.rule("MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) RETURN max(f.sid)") == "f"

    def test_two_expansions_are_not_eligible(self):
        q = (
            "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person), (f)-[:HAS_MEMBER*1..2]-(g) "
            "RETURN DISTINCT g.sid"
        )
        assert self.rule(q) is None

    def test_union_is_judged_branch_by_branch(self):
        """The rule refuses a UNION query as a whole; the translator then translates each branch
        as a query of its own, and a branch is eligible only on its own terms."""
        u = (
            "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person) WHERE p.sid = $personId RETURN DISTINCT f.sid "
            "UNION MATCH (p:Person)-[:KNOWS*1..3]-(f:Person) WHERE p.sid = $personId RETURN {tail}"
        )
        assert self.rule(u.format(tail="DISTINCT f.sid")) is None
        sql = sql_of(u.format(tail="f.sid"), P2)
        assert sql.count("CY_VLP_ENDS") == 1 and sql.count("CY_VLP_PATHS") == 1, sql

    def test_source_in_order_by_is_refused(self):
        q = (
            "MATCH (p:Person)-[:KNOWS*1..2]-(f:Person) RETURN f.lastName AS n, count(DISTINCT f) AS c "
            "ORDER BY p.sid"
        )
        assert self.rule(q) is None
        assert self.rule(q.replace("ORDER BY p.sid", "ORDER BY c")) == "f"
