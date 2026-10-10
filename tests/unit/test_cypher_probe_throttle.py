"""`execute_cypher` probes the connection with `SELECT 1`, but not on every call.

Measured on LOS-shaped queries (docs/research/cypher-stage-timing.md): the probe is one
extra round trip, 0.12 to 0.24 ms, which is 20 to 35% of a point lookup. The failure it
guards against is an idle connection dropped by the network layer (minutes, not
milliseconds), so a probe at most once per idle window covers it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine


def _engine():
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng.conn = MagicMock()
    eng._connection_params = None
    return eng


def _probes(eng) -> int:
    return sum(
        1 for c in eng.conn.cursor.return_value.execute.call_args_list if c.args == ("SELECT 1",)
    )


@pytest.fixture(autouse=True)
def _default_window(monkeypatch):
    monkeypatch.delenv("IVG_PROBE_IDLE_SECONDS", raising=False)


def test_the_first_call_probes():
    eng = _engine()
    eng._probe_if_idle()
    assert _probes(eng) == 1


def test_back_to_back_calls_probe_once():
    eng = _engine()
    for _ in range(50):
        eng._probe_if_idle()
    assert _probes(eng) == 1


def test_a_call_after_the_idle_window_probes_again(monkeypatch):
    import iris_vector_graph.engine as engine_mod

    clock = [1000.0]
    monkeypatch.setattr(engine_mod.time, "monotonic", lambda: clock[0])
    eng = _engine()
    eng._probe_if_idle()
    clock[0] += 29
    eng._probe_if_idle()
    assert _probes(eng) == 1
    clock[0] += 2
    eng._probe_if_idle()
    assert _probes(eng) == 2


def test_zero_restores_a_probe_on_every_call(monkeypatch):
    monkeypatch.setenv("IVG_PROBE_IDLE_SECONDS", "0")
    eng = _engine()
    for _ in range(5):
        eng._probe_if_idle()
    assert _probes(eng) == 5


def test_a_stale_connection_is_still_replaced_when_the_probe_runs(monkeypatch):
    eng = _engine()
    eng._connection_params = {"hostname": "h"}
    eng.conn.cursor.return_value.execute.side_effect = Exception("EPIPE broken pipe")
    fresh = MagicMock()
    import iris

    monkeypatch.setattr(iris.dbapi, "connect", lambda **kw: fresh)
    eng._probe_if_idle()
    assert eng.conn is fresh


def test_a_failed_probe_does_not_start_the_idle_window():
    eng = _engine()
    eng.conn.cursor.return_value.execute.side_effect = Exception("EPIPE")
    with pytest.raises(RuntimeError):
        eng._probe_if_idle()  # cannot reconnect: no params stored
    eng.conn.cursor.return_value.execute.side_effect = None
    eng._probe_if_idle()
    assert _probes(eng) == 2


def test_execute_cypher_uses_the_throttled_probe():
    import inspect

    from iris_vector_graph._engine import query

    src = inspect.getsource(query.QueryMixin.execute_cypher) if hasattr(query, "QueryMixin") else ""
    if not src:
        pytest.skip("mixin name differs")
    assert "_probe_if_idle()" in src and "_reconnect_if_stale()" not in src
