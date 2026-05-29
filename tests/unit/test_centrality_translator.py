"""Spec 162 — Cypher translator unit tests for centrality procedures.

Test-first (Constitution Principle III): tests written BEFORE the translator
implementations in T029 (Degree), T047 (Betweenness), T064 (Closeness),
T080 (Eigenvector). They MUST FAIL until those tasks land.

Currently exercising: T015 _validate_centrality_proc_map (already implemented).
Pending: actual _translate_<algo>_centrality functions.
"""

import os

import pytest

from iris_vector_graph.cypher.translator import (
    CENTRALITY_ALLOWED_KEYS,
    _validate_centrality_proc_map,
)


class TestAllowedKeysContract:
    def test_all_four_procs_have_topk(self):
        for proc in ("ivg.degreeCentrality", "ivg.betweenness",
                     "ivg.closeness", "ivg.eigenvector"):
            assert "topK" in CENTRALITY_ALLOWED_KEYS[proc], \
                f"{proc} must accept topK per FR-024/FR-025"

    def test_betweenness_accepts_mem_budget(self):
        assert "memBudgetMB" in CENTRALITY_ALLOWED_KEYS["ivg.betweenness"], \
            "ivg.betweenness must accept memBudgetMB per FR-026"

    def test_closeness_accepts_formula(self):
        assert "formula" in CENTRALITY_ALLOWED_KEYS["ivg.closeness"]

    def test_eigenvector_accepts_max_iter_and_tol(self):
        keys = CENTRALITY_ALLOWED_KEYS["ivg.eigenvector"]
        assert "maxIter" in keys
        assert "tol" in keys


class TestUnknownParamRejection:
    """FR-029: unknown procedure-call map keys are rejected."""

    def test_weighted_rejected_for_betweenness(self):
        with pytest.raises(ValueError, match="weighted"):
            _validate_centrality_proc_map("ivg.betweenness",
                                           {"sampleSize", "weighted"})

    def test_weighted_rejected_for_closeness(self):
        with pytest.raises(ValueError, match="weighted"):
            _validate_centrality_proc_map("ivg.closeness",
                                           {"formula", "weighted"})

    def test_typo_topk_rejected(self):
        with pytest.raises(ValueError, match="top_k"):
            _validate_centrality_proc_map("ivg.degreeCentrality",
                                           {"direction", "top_k"})

    def test_known_keys_accepted(self):
        _validate_centrality_proc_map("ivg.betweenness",
                                       {"sampleSize", "topK", "memBudgetMB"})

    def test_empty_keys_accepted(self):
        _validate_centrality_proc_map("ivg.degreeCentrality", set())


class TestProcedureCallTranslation:
    """Translator must emit CTEs for CALL ivg.<centrality>(...) — pending T029/T047/T064/T080."""

    def test_translate_degree_centrality_emits_cte_pending(self):
        try:
            from iris_vector_graph.cypher.translator import _translate_degree_centrality
        except ImportError:
            pytest.fail("FR-015: _translate_degree_centrality not yet implemented (T029)")

    def test_translate_betweenness_emits_cte_pending(self):
        try:
            from iris_vector_graph.cypher.translator import _translate_betweenness
        except ImportError:
            pytest.fail("FR-015: _translate_betweenness not yet implemented (T047)")

    def test_translate_closeness_emits_cte_pending(self):
        try:
            from iris_vector_graph.cypher.translator import _translate_closeness
        except ImportError:
            pytest.fail("FR-015: _translate_closeness not yet implemented (T064)")

    def test_translate_eigenvector_emits_cte_pending(self):
        try:
            from iris_vector_graph.cypher.translator import _translate_eigenvector
        except ImportError:
            pytest.fail("FR-015: _translate_eigenvector not yet implemented (T080)")
