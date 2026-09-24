"""`bidirectional=True` has to survive the trip from the engine down to IRIS.

`kg_PERSONALIZED_PAGERANK` accepts `bidirectional` and `reverse_edge_weight`, and both
were being discarded on the path that actually runs. `_engine/algorithms.py` tries the
store first (`self._store.execute_ppr(seed_entities, damping_factor, max_iterations)`)
and returns its rows whenever it comes back without an error, so the two reverse-edge
arguments never reached anything: `IRISGraphStore.execute_ppr` called
`Graph.KG.PageRank.RunJson` with three arguments while the ObjectScript signature takes
five (`PageRank.cls:16` — `bidir As %Integer = 0, revWeight As %Double = 1.0`), and the
Python fallback that does honour them (`:160`) was unreachable.

Measured against the live enterprise container before the fix: with `A -[connects]-> B`
created through `create_edge`, seeding `B` with `bidirectional=True,
reverse_edge_weight=1.0` returned `{'B': 0.176}` — `A` absent. Forward PPR from `A` did
return both nodes, so the traversal worked and only the reverse half was missing. Three
contract tests in `tests/contract/test_ppr_api.py` assert that reverse reachability.

The Arno accelerator is deliberately skipped for a bidirectional request:
`Graph.KG.ArnoAccel.PPRJson` takes seeds, damping and iterations only, so routing there
would silently drop the reverse edges again — the exact failure this file exists to
prevent.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.stores.iris_sql_store import IRISGraphStore


def _make_store(arno_available: bool, capabilities: dict | None = None) -> IRISGraphStore:
    conn = MagicMock()
    store = IRISGraphStore.__new__(IRISGraphStore)
    store.conn = conn
    store._arno_available = arno_available
    store._arno_capabilities = capabilities or {}
    store._nkg_dirty = False
    return store


class TestStorePassesReverseArgsToObjectScript:
    def test_runjson_receives_bidir_and_weight(self):
        store = _make_store(False)
        with patch.object(store, "_call_classmethod", return_value="[]") as mock_cls:
            store.execute_ppr(["node_a"], 0.85, 20, bidirectional=True, reverse_edge_weight=0.5)
        args = mock_cls.call_args[0]
        assert args[0] == "Graph.KG.PageRank"
        assert args[1] == "RunJson"
        assert len(args) == 8, (
            "RunJson takes seedJson, alpha, maxIter, bidir, revWeight, pGraph; "
            f"got {len(args) - 2} value arguments: {args[2:]!r}"
        )
        assert str(args[5]) == "1", f"bidir should be 1 for a bidirectional request, got {args[5]!r}"
        assert float(args[6]) == 0.5

    def test_forward_only_request_still_says_so(self):
        store = _make_store(False)
        with patch.object(store, "_call_classmethod", return_value="[]") as mock_cls:
            store.execute_ppr(["node_a"], 0.85, 20)
        args = mock_cls.call_args[0]
        assert str(args[5]) == "0"
        assert float(args[6]) == 1.0

    def test_bidirectional_skips_arno(self):
        """`ArnoAccel.PPRJson` has no reverse-edge parameters, so it cannot serve this."""
        store = _make_store(True, {"algorithms": ["ppr"]})
        with patch.object(store, "_arno_call", return_value="[]") as mock_arno, patch.object(
            store, "_call_classmethod", return_value="[]"
        ) as mock_cls:
            store.execute_ppr(["node_a"], 0.85, 20, bidirectional=True, reverse_edge_weight=1.0)
        mock_arno.assert_not_called()
        mock_cls.assert_called_once()

    def test_forward_only_still_prefers_arno(self):
        store = _make_store(True, {"algorithms": ["ppr"]})
        with patch.object(store, "_arno_call", return_value="[]") as mock_arno:
            store.execute_ppr(["node_a"], 0.85, 20)
        mock_arno.assert_called_once()


class TestEngineForwardsReverseArgsToTheStore:
    def _engine(self, store):
        from iris_vector_graph._engine.algorithms import AlgorithmsMixin

        engine = AlgorithmsMixin.__new__(AlgorithmsMixin)
        engine._store = store
        engine._store_capabilities = {"ppr": True}
        return engine

    def test_store_call_carries_both_arguments(self):
        from iris_vector_graph.store_protocol import IVGResult

        store = MagicMock()
        store.execute_ppr.return_value = IVGResult(columns=["id", "score"], rows=[["a", 1.0]])
        engine = self._engine(store)

        engine.kg_PERSONALIZED_PAGERANK(
            seed_entities=["b"], bidirectional=True, reverse_edge_weight=0.25
        )

        kwargs = store.execute_ppr.call_args.kwargs
        assert kwargs.get("bidirectional") is True
        assert kwargs.get("reverse_edge_weight") == 0.25

    def test_a_store_without_the_arguments_is_not_used_for_a_bidirectional_request(self):
        """An older store signature must not silently answer a bidirectional query.

        Third-party stores implement `StoreProtocol`; one written against the 3-argument
        `execute_ppr` would raise `TypeError` on the new keywords. Dropping to the Python
        fallback is right — answering forward-only is not.
        """
        from iris_vector_graph.store_protocol import IVGResult

        store = MagicMock()
        store.execute_ppr.side_effect = TypeError(
            "execute_ppr() got an unexpected keyword argument 'bidirectional'"
        )
        engine = self._engine(store)
        engine.capabilities = MagicMock(objectscript_deployed=False, kg_built=False)
        fallback = MagicMock(return_value={"a": 1.0})
        engine._kg_PERSONALIZED_PAGERANK_python_fallback = fallback

        result = engine.kg_PERSONALIZED_PAGERANK(
            seed_entities=["b"], bidirectional=True, reverse_edge_weight=1.0
        )

        assert result == {"a": 1.0}
        assert fallback.call_args[0][5] is True


class TestValidationStillRunsFirst:
    @pytest.mark.parametrize(
        "kwargs,match",
        [
            ({"seed_entities": [], "bidirectional": True}, "at least one entity"),
            (
                {"seed_entities": ["a"], "bidirectional": True, "reverse_edge_weight": -1.0},
                "non-negative",
            ),
        ],
    )
    def test_bad_input_never_reaches_the_store(self, kwargs, match):
        from iris_vector_graph._engine.algorithms import AlgorithmsMixin

        store = MagicMock()
        engine = AlgorithmsMixin.__new__(AlgorithmsMixin)
        engine._store = store
        engine._store_capabilities = {"ppr": True}

        with pytest.raises(ValueError, match=match):
            engine.kg_PERSONALIZED_PAGERANK(**kwargs)
        store.execute_ppr.assert_not_called()
