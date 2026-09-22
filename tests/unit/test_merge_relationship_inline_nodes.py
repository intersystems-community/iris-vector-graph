"""`MERGE (a {id:…})-[:R]->(b {id:…})` has to produce SQL IRIS can prepare.

Measured on the enterprise container, this query never worked:

    MERGE (a:P {id:'x'})-[:LIKES]->(b:P {id:'y'})
    [SQLCODE: <-23>:<Label is not listed among the applicable tables>]
    %msg: Label 'N0' is not listed among the applicable tables^INSERT INTO
          Graph_KG.rdf_edges (s, p, o_id) SELECT ?, ?, ? WHERE NOT EXISTS
          (SELECT 1 FROM rdf_edges WHERE s = n0.node_id AND p = ? AND o_id = n1.node_id)

The idempotency guard is built two ways: from the SQL aliases of MATCH-bound
variables, and from the generated UUIDs of nodes the same query creates. Inline
nodes with a literal `id` are neither, so `src_uuid`/`tgt_uuid` came back None,
the UUID form fell back to the alias form, and the rewrite spliced `n0`/`n1` into
a statement that selects literals and joins nothing.

`MERGE` on variables a preceding `MATCH` bound works and always did, which is why
this stayed hidden: the failing shape is the one-liner.
"""

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _edge_dml(cypher: str):
    """The one `rdf_edges` statement a MERGE emits, with its own parameter row.

    A DML `SQLQuery` carries a list of statements in `sql` and a parallel list of
    parameter rows in `parameters`, matched by position.
    """
    query = translate_to_sql(parse_query(cypher))
    statements = query.sql if isinstance(query.sql, list) else [query.sql]
    edges = [
        (sql, query.parameters[i] if i < len(query.parameters) else [])
        for i, sql in enumerate(statements)
        if "rdf_edges" in sql
    ]
    assert len(edges) == 1, statements
    return edges[0]


class TestInlineNodesProduceAStandaloneGuard:

    def test_the_guard_names_no_alias(self):
        sql, _ = _edge_dml("MERGE (a:P {id:'x'})-[:LIKES]->(b:P {id:'y'})")
        assert "NOT EXISTS" in sql
        # An alias reference in a statement with no FROM is what IRIS rejects.
        for alias in ("n0.", "n1.", "n2."):
            assert alias not in sql, sql

    def test_the_guard_compares_the_same_node_ids_the_insert_writes(self):
        sql, params = _edge_dml("MERGE (a:P {id:'x'})-[:LIKES]->(b:P {id:'y'})")
        assert sql.count("?") == len(params), (sql, params)
        # Both endpoints appear twice: once in the INSERT, once in the guard.
        assert params.count("x") == 2, params
        assert params.count("y") == 2, params
        assert params.count("LIKES") == 2, params

    def test_an_undirected_merge_guards_both_orientations(self):
        sql, params = _edge_dml("MERGE (a:P {id:'x'})-[:LIKES]-(b:P {id:'y'})")
        assert sql.count("?") == len(params), (sql, params)
        assert "OR" in sql.upper()
        assert params.count("x") == 3, params  # insert + both orientations
        assert params.count("y") == 3, params

    def test_bound_variables_still_use_their_aliases(self):
        """The alias form is correct when there is something to alias."""
        sql, _ = _edge_dml(
            "MATCH (a:P {id:'x'}), (b:P {id:'y'}) MERGE (a)-[:KNOWS]->(b)"
        )
        assert "NOT EXISTS" in sql
        assert ".node_id" in sql, sql


class TestAMergeOverADerivedTableGuardsItself:
    """`MATCH (a), (b) MERGE (a)-[:R]->(b)` inserts through a `FROM (…) AS _ge`.

    The outer SELECT of that shape carries no WHERE — every predicate lives inside
    the derived table. The rewrite decided between `WHERE NOT EXISTS` and
    `AND NOT EXISTS` by looking for `" WHERE "` anywhere after the `INSERT INTO`,
    found the derived table's own WHERE, and appended a bare `AND` to a statement
    that had nothing to conjoin it to:

        … ) AS _ge AND NOT EXISTS (…)
        [SQLCODE: <-25>:<Input encountered after end of query>]

    The guard also cannot name `n0`/`n2`: those aliases are scoped to the derived
    table, so from outside it the only columns in scope are `_ge`'s.
    """

    CYPHER = "MATCH (a:P {id:'g'}), (b:P {id:'h'}) MERGE (a)-[:LIKES]->(b)"

    def test_the_guard_is_introduced_by_where_and_not_by_a_bare_and(self):
        sql, _ = _edge_dml(self.CYPHER)
        assert "AS _ge AND" not in sql, sql
        assert "WHERE NOT EXISTS" in sql, sql

    def test_the_guard_reads_the_derived_tables_own_columns(self):
        """The derived table projects `c1`/`c3`; `n0`/`n2` do not reach outside it."""
        sql, _ = _edge_dml(self.CYPHER)
        guard = sql[sql.index("WHERE NOT EXISTS") :]
        assert "_ge.c1" in guard and "_ge.c3" in guard, guard
        for alias in ("n0.", "n2.", "l1.", "l3."):
            assert alias not in guard, guard

    def test_the_markers_and_the_parameters_still_agree(self):
        sql, params = _edge_dml(self.CYPHER)
        assert sql.count("?") == len(params), (sql, params)

    def test_an_undirected_merge_over_a_derived_table_guards_both_orientations(self):
        sql, params = _edge_dml(
            "MATCH (a:P {id:'g'}), (b:P {id:'h'}) MERGE (a)-[:LIKES]-(b)"
        )
        assert "AS _ge AND" not in sql, sql
        guard = sql[sql.index("WHERE NOT EXISTS") :]
        assert "OR" in guard.upper(), guard
        assert sql.count("?") == len(params), (sql, params)
