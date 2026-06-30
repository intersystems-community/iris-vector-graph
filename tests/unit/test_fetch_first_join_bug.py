"""Translator tests for the FETCH FIRST + JOIN %qaqpre crash workaround (build-106).

ROOT CAUSE (proven on a live irishealth:2026.3.0AI.106.0 container): the DB-API driver
SIGSEGVs in %qaqpre when executing a query that combines a multi-table JOIN with
`FETCH FIRST n ROWS ONLY` on VARCHAR-keyed tables (the ivg schema). It is NOT the
INNER JOIN keyword (the original report's guess) — comma-join and LEFT OUTER JOIN crash
too. `SELECT TOP n` does NOT crash and is the fix.

The engine detects the bug at connect time (subprocess probe) and sets
`engine._fetch_first_unsafe`. When set, the translator emits `SELECT TOP n` instead of a
trailing `FETCH FIRST n ROWS ONLY` for LIMIT-only queries.
"""
import pytest
from iris_vector_graph.cypher.translator import translate_to_sql
from iris_vector_graph.cypher.parser import parse_query


class _StubEngine:
    """Minimal engine carrying just the bug flag the translator reads."""
    def __init__(self, fetch_first_unsafe):
        self._fetch_first_unsafe = fetch_first_unsafe
    def get_table_mapping(self, label):
        return None


def _sql(cypher, *, unsafe, params=None):
    ast = parse_query(cypher)
    res = translate_to_sql(ast, params or {}, engine=_StubEngine(unsafe))
    return res if isinstance(res, str) else str(res)


class TestFetchFirstJoinWorkaround:
    CYPHER = "MATCH (n)-[:R]->(m) RETURN n.node_id AS id LIMIT 5"

    def test_unsafe_build_emits_top_not_fetch_first(self):
        sql = _sql(self.CYPHER, unsafe=True)
        assert "FETCH FIRST" not in sql, f"FETCH FIRST must not be emitted on unsafe build:\n{sql}"
        assert "TOP 5" in sql, f"expected SELECT TOP 5:\n{sql}"

    def test_safe_build_keeps_fetch_first(self):
        sql = _sql(self.CYPHER, unsafe=False)
        # On safe builds the existing FETCH FIRST behavior is preserved.
        assert "FETCH FIRST 5 ROWS ONLY" in sql, f"expected FETCH FIRST on safe build:\n{sql}"

    def test_unsafe_limit_only_simple_match(self):
        sql = _sql("MATCH (n) RETURN n.node_id LIMIT 3", unsafe=True)
        assert "FETCH FIRST" not in sql
        assert "TOP 3" in sql

    def test_unsafe_skip_plus_limit_keeps_fetch_first(self):
        # TOP cannot express OFFSET; SKIP+LIMIT must retain FETCH FIRST + OFFSET
        # (rare; residual build-106 risk is documented in apply_pagination).
        sql = _sql("MATCH (n) RETURN n.node_id SKIP 2 LIMIT 4", unsafe=True)
        assert "OFFSET 2" in sql
        assert "FETCH FIRST 4 ROWS ONLY" in sql
        assert "TOP 4" not in sql
