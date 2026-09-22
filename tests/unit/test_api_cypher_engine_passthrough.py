"""`/api/cypher` must hand the engine to the translator, or IRIS crashes the process.

`translate_to_sql(..., engine=...)` is how the `%qaqpre` workaround is reached:
`_apply_pagination` (`cypher/translator.py:3698`) reads
`context._engine._fetch_first_unsafe` and emits `SELECT TOP n` instead of
`FETCH FIRST n ROWS ONLY` on the IRIS AI builds that SIGSEGV on that shape with a
multi-table JOIN over VARCHAR keys.

The router called `translate_to_sql(query_ast, params=request.parameters)` with no
engine, so the flag was always `False` there and `MATCH (p:Protein) RETURN p.name
LIMIT 1` — three JOINs and a `FETCH FIRST` — took the whole uvicorn worker down with
a segfault rather than returning an error. A crash in a subprocess is also why this
is asserted here at the seam instead of by a live request: the live version cannot
report its own failure.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from api.dependencies import get_engine_optional
from api.models.cypher import CypherQueryRequest
from api.routers import cypher as cypher_router


def _request_with_state(**attrs):
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**attrs)))


class TestEngineResolution:
    def test_engine_on_app_state_is_returned(self):
        engine = object()
        assert get_engine_optional(_request_with_state(engine=engine)) is engine

    def test_missing_engine_is_none_not_an_error(self):
        """The endpoint still works without an engine; it just loses the workaround."""
        assert get_engine_optional(_request_with_state()) is None


class TestRouterPassesEngineToTranslator:
    def test_translate_to_sql_receives_the_resolved_engine(self, monkeypatch):
        seen = {}

        def fake_translate(query_ast, params=None, engine=None, procedures=None):
            seen["engine"] = engine
            return SimpleNamespace(
                sql="SELECT TOP 1 1 AS c", parameters=[[]], metadata=None, graph_context=None
            )

        monkeypatch.setattr(cypher_router, "translate_to_sql", fake_translate)

        conn = MagicMock()
        cursor = conn.cursor.return_value
        cursor.description = [("c",)]
        cursor.fetchall.return_value = [(1,)]

        engine = SimpleNamespace(_fetch_first_unsafe=True)

        asyncio.run(
            cypher_router.execute_cypher_query(
                CypherQueryRequest(query="MATCH (p:Protein) RETURN p.name LIMIT 1"),
                db_connection=conn,
                engine=engine,
            )
        )

        assert seen["engine"] is engine

    def test_no_engine_still_translates(self, monkeypatch):
        seen = {}

        def fake_translate(query_ast, params=None, engine=None, procedures=None):
            seen["engine"] = engine
            return SimpleNamespace(
                sql="SELECT 1 AS c", parameters=[[]], metadata=None, graph_context=None
            )

        monkeypatch.setattr(cypher_router, "translate_to_sql", fake_translate)

        conn = MagicMock()
        cursor = conn.cursor.return_value
        cursor.description = [("c",)]
        cursor.fetchall.return_value = [(1,)]

        asyncio.run(
            cypher_router.execute_cypher_query(
                CypherQueryRequest(query="MATCH (p:Protein) RETURN p.name"),
                db_connection=conn,
                engine=None,
            )
        )

        assert seen["engine"] is None


class TestTheCrashingShapeIsAvoidedWhenTheFlagIsSet:
    """End-to-end at the translator seam: same Cypher, two engines, two SQL shapes."""

    @pytest.mark.parametrize(
        "unsafe,expected_present,expected_absent",
        [
            (True, "TOP 1", "FETCH FIRST"),
            (False, "FETCH FIRST 1 ROWS ONLY", "TOP 1"),
        ],
    )
    def test_limit_emission_follows_the_engine_flag(
        self, unsafe, expected_present, expected_absent
    ):
        from iris_vector_graph.cypher.parser import parse_query
        from iris_vector_graph.cypher.translator import translate_to_sql

        # `get_table_mapping` is consulted for every label (translator.py:6139); the
        # ivg schema has no per-label tables, so returning None is the real answer.
        engine = SimpleNamespace(
            _fetch_first_unsafe=unsafe, get_table_mapping=lambda label: None
        )
        sql_query = translate_to_sql(
            parse_query("MATCH (p:Protein) RETURN p.name LIMIT 1"), engine=engine
        )
        sql = sql_query.sql if isinstance(sql_query.sql, str) else "\n".join(sql_query.sql)

        assert expected_present in sql
        assert expected_absent not in sql
