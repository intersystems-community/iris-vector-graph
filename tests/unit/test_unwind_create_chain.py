"""UNWIND range(...) AS x CREATE (...) chained across a WITH boundary
(Aggregation6 [5]).

`_tts_process_parts` expands `UNWIND range(literal, literal) AS i CREATE (...)`
Python-side, one DML set per element. When a later part's own UNWIND range
references a variable an *earlier* part bound per row
(`WITH s, i UNWIND range(0, i) AS j CREATE (s)-[:REL]->()`), that later part
used to run once, globally, after the earlier part's loop had already moved
on to its last row's value of `i` -- so only one relationship was created in
total instead of one batch per outer row (verified against a live namespace:
`UNWIND range(0, 10) AS i CREATE (s:S) WITH s, i UNWIND range(0, i) AS j
CREATE (s)-[:REL]->()` created 1 relationship instead of the expected 66).

`_part_chains_from_unwind_var` detects the pattern and
`_run_unwind_dml_rows`'s `per_row_hook` runs the later part's own expansion
nested inside each row of the earlier part instead.
"""

import re

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import (
    _detect_unwind_dml_expansion,
    _part_chains_from_unwind_var,
    translate_to_sql,
)


def _dml(cypher: str, params=None):
    query = translate_to_sql(parse_query(cypher), params or {})
    statements = query.sql if isinstance(query.sql, list) else [query.sql]
    return [
        (sql, query.parameters[i] if i < len(query.parameters) else [])
        for i, sql in enumerate(statements)
    ]


def _count(stmts, pattern):
    return sum(1 for sql, _ in stmts if re.search(pattern, sql))


class TestPartChainsFromUnwindVar:
    def test_range_bound_on_outer_variable_chains(self):
        q = parse_query(
            "UNWIND range(0, 10) AS i CREATE (s:S) "
            "WITH s, i UNWIND range(0, i) AS j CREATE (s)-[:REL]->()"
        )
        part2 = q.query_parts[1]
        assert _part_chains_from_unwind_var(part2, {"i"}) is True

    def test_range_bound_on_unrelated_variable_does_not_chain(self):
        q = parse_query(
            "UNWIND range(0, 10) AS i CREATE (s:S {n: i}) "
            "WITH s UNWIND range(0, 5) AS j CREATE (s)-[:REL]->()"
        )
        part2 = q.query_parts[1]
        assert _part_chains_from_unwind_var(part2, {"i"}) is False

    def test_no_updating_clause_does_not_chain(self):
        q = parse_query(
            "UNWIND range(0, 10) AS i CREATE (s:S) "
            "WITH s, i UNWIND range(0, i) AS j RETURN j"
        )
        part2 = q.query_parts[1]
        assert _part_chains_from_unwind_var(part2, {"i"}) is False

    def test_two_unwinds_in_chained_part_bail_out(self):
        # Bounded to a single UNWIND in the chained part (no second level of
        # nesting attempted).
        q = parse_query(
            "UNWIND range(0, 10) AS i CREATE (s:S) "
            "WITH s, i UNWIND range(0, i) AS j UNWIND range(0, 1) AS k "
            "CREATE (s)-[:REL]->()"
        )
        part2 = q.query_parts[1]
        assert _part_chains_from_unwind_var(part2, {"i"}) is False


class TestDetectUnwindDmlExpansionResolvesForeachLiterals:
    def test_range_end_bound_resolves_from_foreach_literals(self):
        from iris_vector_graph.cypher.translator import TranslationContext

        q = parse_query("UNWIND range(0, i) AS j CREATE (s)-[:REL]->()")
        part = q.query_parts[0]
        ctx = TranslationContext()
        ctx.foreach_literals = {"i": 3}
        _uc, literals, _all, rows = _detect_unwind_dml_expansion(part, ctx)
        assert [lit.value for lit in literals] == [0, 1, 2, 3]
        assert len(rows) == 4

    def test_range_end_bound_unresolved_without_foreach_literals(self):
        from iris_vector_graph.cypher.translator import TranslationContext

        q = parse_query("UNWIND range(0, i) AS j CREATE (s)-[:REL]->()")
        part = q.query_parts[0]
        ctx = TranslationContext()
        _uc, literals, _all, _rows = _detect_unwind_dml_expansion(part, ctx)
        assert literals is None


class TestUnwindCreateChainSqlShape:
    def test_relationship_count_matches_triangular_number(self):
        # sum(1..3) = 6 relationships, one batch of (i+1) per outer row.
        stmts = _dml(
            "UNWIND range(0, 2) AS i CREATE (s:S9chain) "
            "WITH s, i UNWIND range(0, i) AS j CREATE (s)-[:REL]->()"
        )
        assert _count(stmts, r"INSERT INTO (?:\w+\.)?rdf_edges ") == 6
        # 3 `s` nodes + 6 anonymous relationship targets.
        assert _count(stmts, r"INSERT INTO (?:\w+\.)?nodes ") == 9

    def test_single_row_outer_still_works(self):
        # UNWIND range(0, 0) AS i is a single outer row (i=0): range(0, i)
        # is a single inner row too (j=0) -> exactly 1 relationship.
        stmts = _dml(
            "UNWIND range(0, 0) AS i CREATE (s:S9chain2) "
            "WITH s, i UNWIND range(0, i) AS j CREATE (s)-[:REL]->()"
        )
        assert _count(stmts, r"INSERT INTO (?:\w+\.)?rdf_edges ") == 1

    def test_non_chaining_shape_unaffected(self):
        # Regression guard: an UNWIND+CREATE with no cross-part variable
        # dependency keeps its own (already correct) row count.
        stmts = _dml("UNWIND [0, 1, 2] AS i CREATE (s:S9chain3 {n: i})")
        assert _count(stmts, r"INSERT INTO (?:\w+\.)?nodes ") == 3
