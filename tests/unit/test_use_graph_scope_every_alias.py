"""`USE GRAPH` puts its graph on EVERY graph-owned table alias the generated SQL reads.

docs/KNOWN_ISSUES.md carried an entry saying `USE GRAPH` scoping was built from alias-prefix guesses and
"works for the shapes under test". This is the shapes-not-under-test half: a spread of read queries is
translated offline and the generated SQL is checked alias by alias. For every reference to a graph-owned table
(`nodes`, `rdf_labels`, `rdf_props`, `rdf_edges`, `rdf_reifications`, the embedding tables) the statement must
carry `COALESCE(<alias>.graph_id, '') = COALESCE('<graph>', '')` (or the unaliased `FROM t WHERE
COALESCE(graph_id, '') = ...` of a correlated sub-SELECT). Three further defects are checked because they
come from the same pass:

* a graph predicate on an alias that is not a graph-owned table (a CTE, an UNWIND subquery, a scalar subquery
  has no `graph_id` column: IRIS refuses the statement at Prepare, SQLCODE -29, and the driver's error path
  turns that into zero rows);
* a graph predicate in `WHERE` for a LEFT-joined alias (false for the null row the join exists to keep, so the
  outer join turns inner);
* an unscoped table inside an `EXISTS (...)` / `COUNT` sub-SELECT that lists its tables with commas.

Shapes that leak today are listed in `KNOWN_LEAKS` with the defect each one shows, and are asserted as
`xfail(strict=True)`: when the translator is fixed the strict xfail turns the test red until the entry is
removed, so this file cannot go stale in either direction.
"""

from __future__ import annotations

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

_TABLES = (
    "nodes",
    "rdf_labels",
    "rdf_props",
    "rdf_edges",
    "rdf_reifications",
    "kg_NodeEmbeddings",
    "kg_NodeEmbeddings_optimized",
)
_KEYWORDS = {
    "ON", "WHERE", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "CROSS", "GROUP", "ORDER",
    "UNION", "HAVING", "SET", "AS", "LIMIT", "FETCH", "OFFSET", "AND", "OR", "USING",
}  # fmt: skip
_REF = re.compile(
    r"(?:\bFROM|\bJOIN|,)\s+((?:\w+\.)?(?:%s)|_ue\d+)\b(?:\s+(?:AS\s+)?(\w+))?" % "|".join(_TABLES)
)


def _table_refs(sql: str) -> list:
    """`[(table, alias or None)]` for every graph-owned table (or `_ueN` undirected-edge CTE) the SQL reads.

    The body of an `_ueN` CTE is skipped: it exposes `graph_id` as a column and the statement filters on the
    alias the CTE is joined as, which is the alias this returns.
    """
    body = re.sub(r"WITH _ue\d+ AS \(.*?\n\)\n", "", sql, flags=re.S)
    refs = []
    for m in _REF.finditer(body):
        alias = m.group(2)
        if alias and alias.upper() in _KEYWORDS:
            alias = None
        refs.append((m.group(1).split(".")[-1], alias))
    return refs


def _carries(sql: str, alias, graph: str) -> bool:
    """Whether `sql` puts `graph` on `alias`.

    The plain `alias.graph_id = 'g'` that the relationship-variable path emits is accepted beside the COALESCE
    form: on a schema whose column is `NOT NULL DEFAULT ''` (every fresh install, and any upgraded one that
    `tighten_graph_id_column` could finish) the two agree. They differ only for the default graph on a legacy
    database that still holds NULL spellings, which `tests/unit/test_graph_id_tightening.py` owns. The unaliased
    count fast path binds the graph (`graph_id = ?`) and is accepted for the same reason.
    """
    g = graph.replace("'", "''")
    if alias is None:
        return (
            f"COALESCE(graph_id, '') = COALESCE('{g}', '')" in sql
            or re.search(r"\bFROM \S+ WHERE [^;]*\bgraph_id = \?", sql) is not None
        )
    return (
        f"COALESCE({alias}.graph_id, '') = COALESCE('{g}', '')" in sql
        or f"{alias}.graph_id = '{g}'" in sql
    )


def scope_problems(sql: str, graph: str) -> list:
    """What is wrong with `sql` as a statement scoped to `graph`; empty when nothing is."""
    problems = []
    refs = _table_refs(sql)
    owned = {alias for _, alias in refs if alias}
    for table, alias in refs:
        if not _carries(sql, alias, graph):
            problems.append(f"unscoped {table} {alias or '(unaliased sub-SELECT)'}")
    for m in re.finditer(r"\b(\w+)\.graph_id\s*=", sql):
        alias = m.group(1)
        if alias not in owned:
            problems.append(f"graph predicate on {alias}, which is not a graph-owned table alias")
    for m in re.finditer(r"LEFT (?:OUTER )?JOIN \S+ (\w+)", sql):
        alias = m.group(1)
        for line in sql.splitlines():
            if line.startswith("WHERE") and re.search(rf"\b{alias}\.graph_id\s*=", line):
                problems.append(f"{alias} is LEFT-joined and its graph predicate sits in WHERE")
    return sorted(set(problems))


def _translate(cypher: str) -> str:
    out = translate_to_sql(parse_query(cypher), {})
    return out.sql if isinstance(out.sql, str) else "\n;\n".join(out.sql)


# (id, query body without the USE GRAPH prefix)
SHAPES = [
    ("multi-hop-labels-props", "MATCH (a:Person {name:'x'})-[:KNOWS]->(b:Person)-[:LIKES]->(c) WHERE c.age > 3 RETURN a.name, c.age"),
    ("three-hop-incoming", "MATCH (a)<-[:R]-(b)<-[:S]-(c) RETURN c.id"),
    ("diamond", "MATCH (a:P)-[:R]->(b)<-[:R]-(c:P) WHERE a <> c RETURN DISTINCT a.id, c.id"),
    ("rel-alt-types", "MATCH (a)-[:R|S]->(b) RETURN b.id"),
    ("rel-var-return", "MATCH (a)-[r:KNOWS]->(b) RETURN r, type(r), a.id"),
    ("rel-var-untyped-agg", "MATCH (a)-[r]->(b) RETURN type(r), count(*) AS c"),
    ("rel-var-property", "MATCH (a)-[r:R]->(b) RETURN r.since"),
    ("rel-var-in-where", "MATCH (a)-[r:R]->(b) WHERE r.since > 1 RETURN a.id"),
    ("undirected-typed", "MATCH (a)-[:KNOWS]-(b) RETURN b.name"),
    ("undirected-rel-var", "MATCH (a)-[r:KNOWS]-(b) RETURN b.name"),
    ("undirected-untyped-anchored", "MATCH (a {id:'x'})-[r]-(b) RETURN b.id, type(r)"),
    ("var-length-out", "MATCH (a)-[:KNOWS*1..3]->(b) RETURN b.id"),
    ("var-length-unbounded", "MATCH (a)-[:R*]->(b) RETURN b.id"),
    ("var-length-zero-min-label", "MATCH (a)-[:R*0..2]->(b:Q) RETURN b.name"),
    ("var-length-undirected", "MATCH (a {id:'x'})-[:KNOWS*1..3]-(b) RETURN DISTINCT b.id"),
    ("shortest-path-undirected", "MATCH p = shortestPath((a {id:'x'})-[:R*]-(b {id:'y'})) RETURN p"),
    ("shortest-path-directed-length", "MATCH p = shortestPath((a {id:'x'})-[:R*..5]->(b {id:'y'})) RETURN length(p)"),
    ("with-then-match", "MATCH (a:P) WITH a MATCH (a)-[:R]->(b) RETURN b.name"),
    ("with-aggregate-then-match", "MATCH (a:P)-[:R]->(b) WITH b, count(*) AS c WHERE c > 1 MATCH (b)-[:S]->(d:Q) RETURN d.name, c"),
    ("with-limit-then-labelled-match", "MATCH (a:P) WITH a LIMIT 3 MATCH (a)-[:R]->(b:Q {name:'z'}) RETURN b.id"),
    ("with-rel-var-after-stage", "MATCH (a:P) WITH a MATCH (a)-[r]->(b) RETURN type(r), b.id"),
    ("with-collect-then-property", "MATCH (a:P)-[:R]->(b) WITH a, collect(b.name) AS names RETURN a.id, names"),
    ("with-distinct-then-crossed-node", "MATCH (a:P) WITH DISTINCT a.name AS nm MATCH (b:Q {name: nm}) RETURN b.id"),
    ("with-group-return", "MATCH (a:P) WITH a.name AS n, count(*) AS c RETURN n, c"),
    ("optional-match-edge-label", "MATCH (a:P) OPTIONAL MATCH (a)-[:R]->(b:Q) RETURN a.name, b.name"),
    ("optional-match-rel-var", "MATCH (a:P) OPTIONAL MATCH (a)-[r:R]->(b) RETURN a.name, r"),
    ("optional-match-leading", "OPTIONAL MATCH (a:P) RETURN a.id"),
    ("labels-and-properties-fns", "MATCH (n:P) RETURN labels(n), properties(n), n.name"),
    ("whole-node-return", "MATCH (n) WHERE n.name IS NOT NULL AND n.x = 3 RETURN n"),
    ("exists-property-fn", "MATCH (n) WHERE exists(n.name) RETURN n.id"),
    ("label-predicate", "MATCH (a) WHERE a:P RETURN a.id"),
    ("multi-label", "MATCH (a:P:Q) RETURN a.id"),
    ("cross-product", "MATCH (a), (b) WHERE a.id='x' AND b.id='y' RETURN a, b"),
    ("cross-product-labelled", "MATCH (a:P), (b:Q) WHERE a.x = b.x RETURN a.id, b.id"),
    ("repeated-match", "MATCH (a:P) MATCH (a)-[:R]->(b) MATCH (b)-[:S]->(c) RETURN c.id"),
    ("pattern-predicate-open", "MATCH (a:P) WHERE (a)-[:R]->() RETURN a.id"),
    ("pattern-predicate-labelled-negated", "MATCH (a:P) WHERE NOT (a)-[:R]->(:Q) RETURN a.id"),
    ("order-skip-limit", "MATCH (a:P) RETURN a.name ORDER BY a.name SKIP 1 LIMIT 5"),
    ("count-distinct", "MATCH (a:P) RETURN count(DISTINCT a.name) AS c"),
    ("case-expression", "MATCH (a:P) RETURN a.id, CASE WHEN a.x > 1 THEN a.y ELSE a.z END"),
    ("unwind-after-match", "MATCH (a:P) UNWIND [1,2] AS x RETURN a.id, x"),
    ("call-subquery-count", "MATCH (a:P) CALL { WITH a MATCH (a)-[:R]->(b) RETURN count(b) AS c } RETURN a.id, c"),
    ("with-undirected-after-stage", "MATCH (a:P) WITH a MATCH (a)-[:R]-(b) RETURN b.id"),
    ("with-order-by-stage", "MATCH (a:P) WITH a ORDER BY a.name LIMIT 2 RETURN a.name"),
    ("count-star-fast-path", "MATCH (a:P) RETURN count(*) AS c"),
    ("pattern-comprehension", "MATCH (a:P) RETURN a.id, [(a)-[:R]->(b) | b.name] AS ns"),
    ("exists-subquery", "MATCH (a:P) WHERE EXISTS { MATCH (a)-[:R]->(:Q) } RETURN a.id"),
    ("optional-chain", "MATCH (a:P) OPTIONAL MATCH (a)-[:R]->(b:Q) OPTIONAL MATCH (b)-[:S]->(c) RETURN a.id, c.id"),
    ("vector-search-procedure", "CALL ivg.vector.search('P','embedding',[0.1,0.2],5) YIELD node, score RETURN node, score"),
    ("union-all", "MATCH (a:P) RETURN a.id UNION ALL MATCH (a:P) RETURN a.id"),
    ("union-distinct-with-hops", "MATCH (a:P)-[:R]->(b) RETURN a.id AS i UNION MATCH (c:Q)<-[:S]-(d) RETURN d.id AS i"),
]  # fmt: skip

# Shapes the pass leaves wrong today, and what is wrong. Keep in step with docs/KNOWN_ISSUES.md.
KNOWN_LEAKS: dict = {
    "optional-match-rel-var": (
        "a named relationship variable on an OPTIONAL MATCH gets `e3.graph_id = 'g'` appended to the top-level "
        "WHERE by the alias guess in translate_to_sql's USE GRAPH block; e3 is LEFT OUTER JOINed, so the null row "
        "the OPTIONAL MATCH exists to keep is dropped (the ON clause already carries the predicate)"
    ),
    "optional-match-leading": (
        "the null-row fallback of a leading OPTIONAL MATCH, `SELECT NULL WHERE NOT EXISTS (SELECT 1 FROM "
        "rdf_labels WHERE label = ?)` (_build_null_row_not_exists), is appended after the scope pass and reads "
        "every graph: a label held only by another graph suppresses the null row"
    ),
    "pattern-predicate-labelled-negated": (
        "a labelled pattern predicate (`NOT (a)-[:R]->(:Q)`) is emitted as EXISTS (SELECT 1 FROM nodes n2, "
        "rdf_labels l3, rdf_edges ex4 WHERE ...), a comma-separated FROM that _graph_scope_fragment does not "
        "match; the unlabelled form (`(a)-[:R]->()`) is scoped"
    ),
    "exists-subquery": (
        "`WHERE EXISTS { MATCH (a)-[:R]->(:Q) }` translates to the same comma-FROM sub-SELECT as the labelled "
        "pattern predicate, with no graph predicate on nodes, rdf_labels or rdf_edges"
    ),
    "pattern-comprehension": (
        "`[(a)-[:R]->(b) | b.name]` reads `FROM rdf_edges epc3 JOIN nodes pct4 ON ...` with no graph predicate "
        "on either (only the property fetch inside it is scoped), so it follows edges of every graph"
    ),
    "unwind-after-match": (
        "the USE GRAPH block's alias guess (first alias not starting with n, l or Stage, then break) picks the "
        "UNWIND subquery `u2` and appends `u2.graph_id = 'g'`; the subquery has no graph_id column, so IRIS "
        "refuses the statement at Prepare (SQLCODE -29) and the driver turns that into zero rows"
    ),
    "call-subquery-count": (
        "same alias guess picks the correlated CALL {} scalar subquery and appends `scalar.graph_id = 'g'` "
        "(no such alias: Prepare error), and the subquery's own rdf_edges / nodes carry no predicate"
    ),
    "with-order-by-stage": (
        "ORDER BY a.name on a WITH stage builds its sort key as `(SELECT val FROM rdf_props WHERE s = "
        "n0.node_id AND \"key\" = 'name')` inside the __ob wrapper, after the scope pass: the sort value can come "
        "from another graph's property row for the same node ID"
    ),
    "union-all": (
        "_tts_union_branches builds each branch as a fresh ast.CypherQuery without graph_context, so "
        "`USE GRAPH 'g' ... UNION ALL ...` scopes no branch at all (the all-graphs correlation applies instead)"
    ),
    "union-distinct-with-hops": "same as union-all: UNION drops graph_context from every branch",
}


def _params():
    for shape_id, body in SHAPES:
        marks = []
        if shape_id in KNOWN_LEAKS:
            marks.append(pytest.mark.xfail(strict=True, reason=KNOWN_LEAKS[shape_id]))
        yield pytest.param(shape_id, body, id=shape_id, marks=marks)


@pytest.mark.parametrize("graph", ["g1", ""])
@pytest.mark.parametrize("shape_id,body", list(_params()))
def test_every_graph_owned_alias_carries_the_graph(shape_id, body, graph):
    cypher = f"USE GRAPH '{graph}' " + body
    sql = _translate(cypher)
    assert _table_refs(sql), f"the checker found no graph-owned table in:\n{sql}"
    assert scope_problems(sql, graph) == [], sql


class TestTheCheckerSeesWhatItClaims:
    """A checker that passes everything would make the table above green and worthless."""

    def test_a_missing_predicate_is_reported(self):
        sql = "SELECT 1\nFROM nodes n0\nJOIN rdf_labels l1 ON l1.s = n0.node_id AND l1.label = ?\nWHERE 1=1"
        assert scope_problems(sql, "g") == ["unscoped nodes n0", "unscoped rdf_labels l1"]

    def test_a_comma_listed_sub_select_is_read(self):
        sql = (
            "SELECT 1\nFROM nodes n0\nWHERE COALESCE(n0.graph_id, '') = COALESCE('g', '') AND EXISTS "
            "(SELECT 1 FROM nodes n2, rdf_edges ex4 WHERE ex4.s = n0.node_id)"
        )
        assert scope_problems(sql, "g") == ["unscoped nodes n2", "unscoped rdf_edges ex4"]

    def test_a_predicate_on_a_subquery_alias_is_reported(self):
        sql = (
            "SELECT 1\nFROM nodes n0\nCROSS JOIN (SELECT ? AS x) u2\n"
            "WHERE COALESCE(n0.graph_id, '') = COALESCE('g', '') AND u2.graph_id = 'g'"
        )
        assert scope_problems(sql, "g") == ["graph predicate on u2, which is not a graph-owned table alias"]

    def test_a_left_joined_predicate_in_where_is_reported(self):
        sql = (
            "SELECT 1\nFROM nodes n0\nLEFT OUTER JOIN rdf_edges e3 ON e3.s = n0.node_id AND "
            "COALESCE(e3.graph_id, '') = COALESCE('g', '')\n"
            "WHERE COALESCE(n0.graph_id, '') = COALESCE('g', '') AND e3.graph_id = 'g'"
        )
        assert scope_problems(sql, "g") == ["e3 is LEFT-joined and its graph predicate sits in WHERE"]

    def test_a_fully_scoped_statement_is_clean(self):
        sql = (
            "SELECT 1\nFROM nodes n0\nJOIN rdf_edges e1 ON e1.s = n0.node_id AND "
            "COALESCE(e1.graph_id, '') = COALESCE('g', '')\n"
            "WHERE COALESCE(n0.graph_id, '') = COALESCE('g', '')"
        )
        assert scope_problems(sql, "g") == []
