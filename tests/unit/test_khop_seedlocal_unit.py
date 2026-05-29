"""Spec 164 — Unit tests for k-hop seed-local routing decisions.

These tests use mocked IRIS objects to verify the routing layer's behavior
WITHOUT needing a live container. The actual ObjectScript walk and IRIS
integration are tested e2e in tests/e2e/test_khop_seedlocal_e2e.py.

Tests:
    test_routing_dispatches_seedlocal_for_hops_1 — T007
    test_routing_dispatches_rust_for_hops_3 — T019
    test_routing_respects_env_var — T020
    test_translator_emits_correct_cte — T033
    test_unknown_proc_key_rejects_with_value_error — T034
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


def _make_engine_with_mocked_classmethod(classmethod_return: str = '[]'):
    """Return an IRISGraphEngine whose `_call_classmethod` is mocked.

    Skips IRIS connection setup; useful for routing-decision tests that
    don't touch the database.
    """
    from iris_vector_graph.engine import IRISGraphEngine
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng._call_classmethod = MagicMock(return_value=classmethod_return)
    eng._call_classmethod_large = MagicMock(return_value=classmethod_return)
    eng.conn = MagicMock()
    eng._iris_obj = MagicMock(return_value=MagicMock())
    eng._detect_arno = MagicMock(return_value=False)
    eng._arno_capabilities = {"algorithms": []}
    eng._arno_call = MagicMock()
    eng.capabilities = MagicMock()
    eng.capabilities.objectscript_deployed = True
    return eng


class TestSeedLocalRouting:
    def test_routing_dispatches_seedlocal_for_hops_1(self):
        """T007 — engine.khop(seed, hops=1) must route to KHopNeighborsSeedLocal."""
        eng = _make_engine_with_mocked_classmethod('[{"node_id":"x","hops":1}]')
        with patch.object(eng, "_iris_obj") as mock_iris_obj_factory:
            mock_iris_inst = MagicMock()
            mock_iris_inst.get.return_value = "1"
            mock_iris_obj_factory.return_value = mock_iris_inst
            result = eng.khop("seed", hops=1)
        assert result.get("path") == "seedlocal", \
            f"Expected path=seedlocal for hops=1, got {result.get('path')!r}"

    def test_routing_dispatches_rust_for_hops_3(self):
        """T019 — engine.khop(seed, hops=3) must NOT route to seedlocal."""
        eng = _make_engine_with_mocked_classmethod('{"layers":[]}')
        result = eng.khop("seed", hops=3)
        assert result.get("path") != "seedlocal", \
            f"hops=3 should route to Rust/fallback, got path={result.get('path')!r}"

    def test_routing_respects_env_var(self, monkeypatch):
        """T020 — IVG_KHOP_SEEDLOCAL_MAX_HOPS=3 should let hops=3 take seedlocal."""
        monkeypatch.setenv("IVG_KHOP_SEEDLOCAL_MAX_HOPS", "3")
        eng = _make_engine_with_mocked_classmethod('[]')
        with patch.object(eng, "_iris_obj") as mock_iris_obj_factory:
            mock_iris_inst = MagicMock()
            mock_iris_inst.get.return_value = "1"
            mock_iris_obj_factory.return_value = mock_iris_inst
            result = eng.khop("seed", hops=3)
        assert result.get("path") == "seedlocal", \
            f"Env var should enable seedlocal at hops=3, got {result.get('path')!r}"


class TestKhopSeedlocalCypherTranslator:
    def test_translator_emits_correct_cte(self):
        """T033 — _translate_khop_seedlocal generates the contracted CTE SQL."""
        from iris_vector_graph.cypher import translator
        from iris_vector_graph.cypher.ast import CypherProcedureCall

        proc = CypherProcedureCall(
            procedure_name="ivg.khopSeedLocal",
            arguments=[],
            options={"seed": "alice", "hops": 1, "predicate": "", "maxResults": 10},
            yield_items=["node", "hops"],
        )
        ctx = MagicMock()
        ctx.all_stage_params = []
        ctx.stages = []
        ctx.variable_aliases = {}
        ctx.scalar_variables = set()

        translator._translate_khop_seedlocal(proc, ctx)

        joined_sql = "\n".join(ctx.stages)
        assert "kg_KHopSeedLocal" in joined_sql, "missing kg_KHopSeedLocal SQL function ref"
        assert "JSON_TABLE" in joined_sql, "expected JSON_TABLE projection"
        assert "alice" in str(ctx.all_stage_params), "seed param not bound"
        assert ctx.variable_aliases.get("node") == "KHopSeedLocal"
        assert ctx.variable_aliases.get("hops") == "KHopSeedLocal"

    def test_unknown_proc_key_rejects_with_value_error(self):
        """T034 — unknown keys (e.g., typo `seedID`) must raise ValueError per FR-015."""
        from iris_vector_graph.cypher import translator
        from iris_vector_graph.cypher.ast import CypherProcedureCall

        proc = CypherProcedureCall(
            procedure_name="ivg.khopSeedLocal",
            arguments=[],
            options={"seedID": "alice", "hops": 1},
            yield_items=["node", "hops"],
        )
        ctx = MagicMock()

        with pytest.raises(ValueError, match=r"Unknown.*seedID"):
            translator._translate_khop_seedlocal(proc, ctx)
