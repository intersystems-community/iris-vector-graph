"""A CREATE after a MATCH may only write what the MATCH's rows license.

Measured before the fix:

    MATCH (a:P {id:'TXQ:missing'}) CREATE (a)-[:R]->(b:P {id:'TXQ:new'})

created node `TXQ:new` with its label and property rows, added no edge, and reported
no error. openCypher is explicit that a CREATE runs once per incoming row, so a MATCH
that binds nothing means nothing is created at all.

The edge insert was always correlated with the MATCH — it selects from the matched
rows, so zero rows wrote zero edges. Only the inline nodes were emitted as
unconditional `SELECT <literal> WHERE NOT EXISTS (...)` statements, which is why the
half-written result had a node and no edge.

Row multiplicity is a separate, still-open deviation: a MATCH returning N rows creates
one node here, not N. These tests pin the zero-row case, which is the one that writes
data openCypher says must not exist.
"""

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _writes_to(sql: str, table: str) -> bool:
    """Whether this statement inserts into `table`, with or without a schema prefix.

    The translator qualifies table names from a schema prefix that other tests in the
    suite set and clear, so `INSERT INTO nodes` and `INSERT INTO Graph_KG.nodes` are
    the same statement as far as these assertions are concerned.
    """
    return re.search(rf"INSERT INTO (?:\w+\.)?{table} ", sql) is not None


def _dml(cypher: str):
    query = translate_to_sql(parse_query(cypher))
    statements = query.sql if isinstance(query.sql, list) else [query.sql]
    return [
        (sql, query.parameters[i] if i < len(query.parameters) else [])
        for i, sql in enumerate(statements)
    ]


MATCH_THEN_CREATE = (
    "MATCH (a:P {id:'TXQ:missing'}) CREATE (a)-[:R]->(b:P {id:'TXQ:new'})"
)


class TestEveryWriteIsCorrelatedWithTheMatch:

    def test_no_statement_writes_without_reading_the_match(self):
        for sql, _ in _dml(MATCH_THEN_CREATE):
            # The edge insert already selected from the match and puts FROM on its own
            # line; the node writes now join the same rows in as a derived table.
            assert "FROM " in sql, sql

    def test_the_node_label_and_prop_inserts_are_all_gated(self):
        seen = set()
        for sql, params in _dml(MATCH_THEN_CREATE):
            for table in ("nodes", "rdf_labels", "rdf_props"):
                if _writes_to(sql, table):
                    seen.add(table)
                    # The gate is the matched row set, joined in as a derived table.
                    assert "_cg" in sql, sql
                    assert "TXQ:missing" in sql or "TXQ:missing" in params, (sql, params)
        assert seen == {"nodes", "rdf_labels", "rdf_props"}, seen

    def test_the_gated_insert_collapses_duplicate_match_rows(self):
        """One matched node cannot become two identical inserts."""
        for sql, _ in _dml(MATCH_THEN_CREATE):
            if _writes_to(sql, "nodes"):
                assert "SELECT DISTINCT" in sql, sql


class TestAPlainCreateStaysUngated:
    """With no MATCH there is nothing to correlate, and no reason to pay for a join."""

    def test_a_bare_create_needs_no_derived_table(self):
        for sql, _ in _dml("CREATE (b:P {id:'TXQ:plain'})"):
            assert "_cg" not in sql, sql

    def test_a_create_relationship_pattern_stays_ungated(self):
        for sql, _ in _dml(
            "CREATE (a:P {id:'TXQ:p1'})-[:R]->(b:P {id:'TXQ:p2'})"
        ):
            assert "_cg" not in sql, sql
