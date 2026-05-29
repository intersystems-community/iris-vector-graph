"""Spec 166 unit tests — shared LazyKG instance on IRISGraphEngine."""
from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest


def _make_engine():
    from iris_vector_graph.engine import IRISGraphEngine
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng.conn = MagicMock()
    eng._shared_lkg = None
    eng._arno_available = False
    eng._arno_capabilities = {}
    eng._nkg_dirty = False
    eng.capabilities = MagicMock()
    eng.capabilities.objectscript_deployed = False
    return eng


class TestSharedLazyKG:
    def test_second_algo_reuses_shared_lkg(self):
        """T001 / AS-166-1 / NFR-166-001 — second algorithm reuses same LazyKG instance."""
        from iris_vector_graph.stores.lazy_kg import LazyKG
        eng = _make_engine()

        init_count = [0]
        original_init = LazyKG.__init__

        def counting_init(self, conn, include_sinks=True):
            init_count[0] += 1
            self._iris = MagicMock()
            self._out_cache = {}
            self._in_cache = {}
            self._degree_cache = {}
            self._degp_cache = {}
            self._in_degree_cache = {}
            self._in_degp_cache = {}
            self._nodes_cache = None
            self.include_sinks = include_sinks

        with patch.object(LazyKG, '__init__', counting_init):
            lkg1 = eng._get_shared_lkg()
            lkg2 = eng._get_shared_lkg()

        assert init_count[0] == 1, (
            f"LazyKG.__init__ called {init_count[0]} times; expected 1 (shared instance)"
        )
        assert lkg1 is lkg2, "Both calls should return the same object"

    def test_shared_lkg_cleared_on_invalidate(self):
        """T002 / AS-166-2 — _invalidate_shared_lkg() clears the cache."""
        eng = _make_engine()
        from iris_vector_graph.stores.lazy_kg import LazyKG
        eng._shared_lkg = MagicMock(spec=LazyKG)
        assert eng._shared_lkg is not None
        eng._invalidate_shared_lkg()
        assert eng._shared_lkg is None
