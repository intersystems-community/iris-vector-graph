"""Spec 163 — Cypher translator unit tests for community-detection procedures.

Test-first: T052-T056 implementations land later. FR-015 unknown-key rejection
already works (T013/T014 in Phase 2).
"""

import pytest

from iris_vector_graph.cypher.translator import (
    COMMUNITY_ALLOWED_KEYS,
    _validate_community_proc_map,
)


class TestAllowedKeysContract:
    def test_all_four_procs_have_topk(self):
        for proc in ("ivg.leiden", "ivg.triangleCount", "ivg.scc", "ivg.kcore"):
            assert "topK" in COMMUNITY_ALLOWED_KEYS[proc]

    def test_leiden_accepts_gamma_seed_membudget(self):
        for key in ("gamma", "randomSeed", "memBudgetMB", "maxLevels", "tol"):
            assert key in COMMUNITY_ALLOWED_KEYS["ivg.leiden"]


class TestUnknownParamRejection:
    """FR-015: unknown procedure-call map keys are rejected."""

    def test_weighted_rejected_for_leiden(self):
        with pytest.raises(ValueError, match="weighted"):
            _validate_community_proc_map("ivg.leiden", {"gamma", "weighted"})

    def test_weighted_rejected_for_triangle(self):
        with pytest.raises(ValueError, match="weighted"):
            _validate_community_proc_map("ivg.triangleCount", {"weighted"})

    def test_typo_top_k_rejected(self):
        with pytest.raises(ValueError, match="top_k"):
            _validate_community_proc_map("ivg.scc", {"top_k"})

    def test_known_keys_accepted(self):
        _validate_community_proc_map("ivg.leiden",
                                      {"gamma", "topK", "randomSeed", "memBudgetMB"})

    def test_empty_keys_accepted(self):
        _validate_community_proc_map("ivg.kcore", set())


class TestProcedureCallTranslationPending:
    """Translator handlers pending T052-T055."""

    def test_translate_leiden_emits_cte_pending(self):
        try:
            from iris_vector_graph.cypher.translator import _translate_leiden  # noqa: F401
        except ImportError:
            pytest.fail("_translate_leiden not yet implemented (T052)")

    def test_translate_triangle_count_pending(self):
        try:
            from iris_vector_graph.cypher.translator import _translate_triangle_count  # noqa: F401
        except ImportError:
            pytest.fail("_translate_triangle_count not yet implemented (T053)")

    def test_translate_scc_pending(self):
        try:
            from iris_vector_graph.cypher.translator import _translate_scc  # noqa: F401
        except ImportError:
            pytest.fail("_translate_scc not yet implemented (T054)")

    def test_translate_kcore_pending(self):
        try:
            from iris_vector_graph.cypher.translator import _translate_kcore  # noqa: F401
        except ImportError:
            pytest.fail("_translate_kcore not yet implemented (T055)")
