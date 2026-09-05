"""Spec 213 — unit tests for iris_vector_graph.ledger.guard (no IRIS required).

T050 (US5): strict-mode guard with a mocked engine; TTL cache; refresh; absent class; Count.
T099 (US11): TestTemporalMirror. T105: TestMetricsHook.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from iris_vector_graph.ledger import guard as G
from iris_vector_graph.ledger.errors import LedgerStrictModeError

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"


class FakeIris:
    def __init__(self, exists=1, meta=None, raise_on_meta=False):
        self.exists = exists
        self.meta = meta or {"ok": True, "state": "never-enabled", "strict": False}
        self.raise_on_meta = raise_on_meta
        self.calls = []

    def classMethodValue(self, cls, method, *args):
        self.calls.append((cls, method, args))
        if cls == "%Dictionary.CompiledClass" and method == "%ExistsId":
            return self.exists
        if cls == "Graph.KG.Ledger" and method == "Meta":
            if self.raise_on_meta:
                raise RuntimeError("boom")
            return json.dumps(self.meta)
        if cls == "Graph.KG.Ledger" and method == "Count":
            return 1
        return ""


def _engine(fake):
    eng = MagicMock()
    eng._iris_obj.return_value = fake
    return eng


class TestGuard:
    def test_raises_only_when_enabled_and_strict(self):
        fake = FakeIris(meta={"ok": True, "state": "enabled", "strict": True})
        g = G.LedgerGuard(_engine(fake))
        with pytest.raises(LedgerStrictModeError):
            g.check_structural_write("create_node")
        assert any(c[1] == "Count" and c[2] == ("rej_strict_block",) for c in fake.calls)

    @pytest.mark.parametrize(
        "meta",
        [
            {"ok": True, "state": "enabled", "strict": False},
            {"ok": True, "state": "disabled", "strict": True},
            {"ok": True, "state": "never-enabled", "strict": False},
        ],
    )
    def test_no_raise_otherwise(self, meta):
        fake = FakeIris(meta=meta)
        g = G.LedgerGuard(_engine(fake))
        g.check_structural_write("create_node")  # no exception
        assert not any(c[1] == "Count" for c in fake.calls)

    def test_absent_class_is_noop_and_never_calls_meta(self):
        fake = FakeIris(exists=0, meta={"ok": True, "state": "enabled", "strict": True})
        g = G.LedgerGuard(_engine(fake))
        g.check_structural_write("create_edge")
        assert not any(c[1] == "Meta" for c in fake.calls)
        assert g.state() == "never-enabled"

    def test_probe_failure_fails_open(self):
        fake = FakeIris(raise_on_meta=True)
        g = G.LedgerGuard(_engine(fake))
        g.check_structural_write("x")
        assert g.is_strict() is False

    def test_engine_without_iris_obj_fails_open(self):
        eng = MagicMock()
        eng._iris_obj.side_effect = TypeError("no native connection")
        g = G.LedgerGuard(eng)
        g.check_structural_write("x")
        assert g.is_enabled() is False

    def test_cache_honours_ttl_and_refresh(self):
        fake = FakeIris(meta={"ok": True, "state": "enabled", "strict": False})
        g = G.LedgerGuard(_engine(fake), ttl=5.0)
        with patch("iris_vector_graph.ledger.guard.time.monotonic") as mono:
            mono.return_value = 100.0
            g.check_structural_write("a")
            g.check_structural_write("b")
            assert sum(1 for c in fake.calls if c[1] == "Meta") == 1
            mono.return_value = 104.0
            g.check_structural_write("c")
            assert sum(1 for c in fake.calls if c[1] == "Meta") == 1
            mono.return_value = 106.0
            g.check_structural_write("d")
            assert sum(1 for c in fake.calls if c[1] == "Meta") == 2
            fake.meta = {"ok": True, "state": "enabled", "strict": True}
            g.check_structural_write("e")  # still cached → no raise
            g.refresh()
            with pytest.raises(LedgerStrictModeError):
                g.check_structural_write("f")

    def test_count_rejection_swallows_errors(self):
        eng = MagicMock()
        eng._iris_obj.side_effect = RuntimeError("down")
        G.LedgerGuard(eng).count_rejection("rej_strict_block")  # no exception


class TestTemporalMirror:
    """US11 / FR-044a: the structural mirror of a temporal insert is skipped under strict mode."""

    def _mixin(self, strict):
        from iris_vector_graph._engine.temporal import TemporalMixin

        class E(TemporalMixin):
            def _t(self, name):
                return f"Graph_KG.{name}"

        e = E()
        e._store = MagicMock()
        e._store.write_temporal_edge.return_value = MagicMock(error=None)
        e.conn = MagicMock()
        guard = MagicMock()
        guard.is_strict.return_value = strict
        e._ledger_guard = guard
        return e

    def _inserts(self, e):
        return [
            c.args[0]
            for c in e.conn.cursor.return_value.execute.call_args_list
            if "rdf_edges" in str(c.args[0])
        ]

    def test_strict_skips_mirror_but_writes_temporal(self):
        e = self._mixin(strict=True)
        assert e.create_edge_temporal("a", "T", "b", timestamp=1000, graph="g") is True
        e._store.write_temporal_edge.assert_called_once()
        assert self._inserts(e) == []

    def test_default_mode_writes_mirror(self):
        e = self._mixin(strict=False)
        assert e.create_edge_temporal("a", "T", "b", timestamp=1000, graph="g") is True
        assert len(self._inserts(e)) == 1

    def test_no_graph_never_consults_guard(self):
        e = self._mixin(strict=True)
        e.create_edge_temporal("a", "T", "b", timestamp=1000)
        e._ledger_guard.is_strict.assert_not_called()
        assert self._inserts(e) == []


class TestMetricsHook:
    """FR-052: registered hooks receive one event per commit outcome; failures are swallowed."""

    def _ledger(self, commit_response):
        from iris_vector_graph.ledger.client import GraphLedger
        from tests.unit.test_ledger_client import FakeServer

        srv = FakeServer(commit_response=commit_response)
        eng = MagicMock()
        eng._iris_obj.return_value = srv
        eng._schema_prefix = "Graph_KG"
        return GraphLedger(eng), srv

    def _cs(self):
        from iris_vector_graph.ledger import Changeset

        cs = Changeset(actor="a", actor_type="human")
        cs.create_node("n")
        return cs

    def test_hook_receives_success_event(self):
        led, _ = self._ledger(None)
        events = []
        led.register_metrics_hook(events.append)
        led.commit(self._cs())
        assert len(events) == 1
        e = events[0]
        assert e["outcome"] == "success" and e["actor"] == "a" and e["op_count"] == 1
        assert e["revision_id"] and isinstance(e["duration_ms"], float)

    def test_hook_receives_rejection_event(self):
        from iris_vector_graph.ledger.errors import StaleHeadError

        led, _ = self._ledger({"ok": False, "error": "stale_head", "reason": "s", "head": "d" * 32})
        events = []
        led.register_metrics_hook(events.append)
        with pytest.raises(StaleHeadError):
            led.commit(self._cs())
        assert (
            events[0]["outcome"] == "stale_head"
            and events[0]["revision_id"] is None
            and events[0]["reason"] == "s"
        )

    def test_unregistered_hook_not_called_and_failing_hook_is_swallowed(self, caplog):
        led, _ = self._ledger(None)
        led.commit(self._cs())  # no hooks registered → nothing to assert but no error

        def boom(_):
            raise RuntimeError("hook down")

        led.register_metrics_hook(boom)
        import logging

        with caplog.at_level(logging.WARNING, logger="iris_vector_graph.ledger"):
            res = led.commit(self._cs())
        assert res.revision is not None
        assert any("hook failed" in r.getMessage() for r in caplog.records)
