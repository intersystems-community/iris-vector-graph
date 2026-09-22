"""Spec 230 — `approx_count_distinct` finds its source, or says it could not.

Found by the T074 gate. `MATCH (a {node_id: 'star:c'})-[:SPOKE*1..2]-(b) RETURN
approx_count_distinct(b) AS c` answered `0` for a node with four neighbours, with
`warnings=[]` and `sql=''`. The route resolves its source node by scanning the
translated statement's bound parameters for a string, and a node ID written as a
literal in the pattern is inlined into the SQL rather than bound — so nothing is
found, and the unresolved case returns `0` as though it were a count.

Two things are wrong and both are fixed here: a literal-bound source is now
resolved (the shared `extract_vlp_source_ids` learns the literal spelling that sits
beside the `= ?` one it already knew), and a source that genuinely cannot be resolved
says so in the result's warnings instead of reporting a silent zero.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph._engine.query import extract_vlp_source_ids
from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _engine(store=None):
    from iris_vector_graph.engine import IRISGraphEngine

    engine = IRISGraphEngine.__new__(IRISGraphEngine)
    engine._store = store if store is not None else MagicMock()
    engine._nkg_dirty = False
    engine.conn = MagicMock()
    return engine


_LITERAL_CYPHER = (
    "MATCH (a {node_id: 'star:c'})-[:SPOKE*1..2]-(b) "
    "RETURN approx_count_distinct(b) AS c"
)


class TestALiteralSourceIsResolvedWithoutTheDatabase:
    def test_extract_reads_a_cast_literal_binding(self):
        sql_query = translate_to_sql(parse_query(_LITERAL_CYPHER), {})
        vl = sql_query.var_length_paths[0]
        ids = extract_vlp_source_ids(
            sql_query=sql_query,
            source_labels=[],
            source_alias=vl["source_alias"],
            target_alias=vl["target_alias"],
            store=MagicMock(),
        )
        assert ids == ["star:c"], f"{ids}\n{sql_query.sql}"

    def test_a_bound_parameter_still_wins(self):
        """The `= ?` spelling is the common one and must not regress."""
        cypher = (
            "MATCH (a {node_id: $src})-[:SPOKE*1..2]-(b) "
            "RETURN approx_count_distinct(b) AS c"
        )
        sql_query = translate_to_sql(parse_query(cypher), {"src": "star:c"})
        vl = sql_query.var_length_paths[0]
        ids = extract_vlp_source_ids(
            sql_query=sql_query,
            source_labels=[],
            source_alias=vl["source_alias"],
            target_alias=vl["target_alias"],
            store=MagicMock(),
        )
        assert ids == ["star:c"], f"{ids}\n{sql_query.sql}"


class TestTheCountIsAnsweredFromTheResolvedSource:
    def test_a_literal_source_reaches_countdistinctkhop(self):
        engine = _engine()
        with patch("iris_vector_graph.schema._call_classmethod") as call:
            call.return_value = '{"estimate": 4, "registers": 256, "std_error": 0.065}'
            result = engine.execute_cypher(_LITERAL_CYPHER)
        assert result.rows == [[4]], result.rows
        assert call.call_args[0][3] == "star:c", call.call_args
        assert any("approx" in w.lower() for w in result.metadata.warnings), (
            result.metadata.warnings
        )


class TestAnUnresolvedSourceIsNotReportedAsZero:
    def test_the_warning_says_the_source_was_not_resolved(self):
        engine = _engine()
        result = engine.execute_cypher(
            "MATCH (a)-[:SPOKE*1..2]-(b) RETURN approx_count_distinct(b) AS c"
        )
        warnings = result.metadata.warnings if result.metadata else []
        assert any("source" in w.lower() for w in warnings), warnings
        assert any("approx_count_distinct" in w for w in warnings), warnings
