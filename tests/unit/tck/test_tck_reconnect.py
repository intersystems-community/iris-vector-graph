"""4.6.0 R311: a lost connection fails the scenario that hit it, once, and the run goes on (fixes67).

A full TCK on release cef6a3e1 under 5.4.1 lost one shard's connection (`<COMMUNICATION ERROR> Message out of order`,
then `Connection closed`); every later scenario on that shard failed in teardown, 81 false failures. The harness now
reconnects when a teardown fails that way, redoes the teardown on the fresh connection, fails that one scenario, and
logs the reconnect, so a run that needed one says so.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


def _engine(cursor_error=None):
    cursor = MagicMock()
    cursor.fetchall.return_value = [("n1",)]
    if cursor_error is not None:
        cursor.execute.side_effect = cursor_error
    conn = MagicMock()
    conn.cursor.return_value = cursor
    engine = MagicMock()
    engine._schema_prefix = "Graph_KG"
    engine._store = SimpleNamespace(conn=conn)
    return engine, conn, cursor


def _context(monkeypatch, first_error):
    import tests.tck.environment as env

    dead, dead_conn, _ = _engine(first_error)
    fresh, fresh_conn, fresh_cursor = _engine()
    made = []

    def connect(container, port):
        made.append((container, port))
        return fresh_conn

    def engine_for(conn, **_):
        assert conn is fresh_conn
        return fresh

    monkeypatch.setattr(env, "_connect", connect)
    monkeypatch.setattr(env, "_new_engine", engine_for)
    monkeypatch.setattr(env, "_enable_tck_multigraph", lambda ctx: None)
    ctx = SimpleNamespace(
        engine=dead, conn=dead_conn, scenario_label="TCK_abcd1234", _tck_target=("ivg-iris-enterprise", 31972),
    )
    return ctx, fresh, fresh_cursor, made, dead_conn


LOST = RuntimeError("<COMMUNICATION ERROR> Message out of order; Details: expected: 206507 got: -1")


def test_a_lost_connection_reconnects_redoes_the_teardown_and_fails_that_scenario_once(monkeypatch, caplog):
    import logging

    import tests.tck.environment as env

    ctx, fresh, fresh_cursor, made, dead_conn = _context(monkeypatch, LOST)
    scenario = SimpleNamespace(name="Merge5 [11]")
    with caplog.at_level(logging.WARNING, logger=env.logger.name):
        with pytest.raises(env.TeardownFailed, match="reconnected"):
            env.after_scenario(ctx, scenario)
    assert made == [("ivg-iris-enterprise", 31972)]
    assert ctx.engine is fresh and ctx.conn is fresh._store.conn
    assert ctx.tck_reconnects == 1
    dead_conn.close.assert_called_once()
    # the teardown ran again on the fresh connection
    fresh.execute_cypher.assert_called_once()
    assert any(c.args[0].startswith("DELETE") for c in fresh_cursor.execute.call_args_list)
    assert any("TCK reconnect" in r.getMessage() and "Merge5 [11]" in r.getMessage() for r in caplog.records)


def test_the_next_scenario_runs_on_the_fresh_connection(monkeypatch):
    import tests.tck.environment as env

    ctx, fresh, _, made, _ = _context(monkeypatch, LOST)
    with pytest.raises(env.TeardownFailed):
        env.after_scenario(ctx, SimpleNamespace(name="first"))
    ctx.scenario_label = "TCK_00000002"
    env.after_scenario(ctx, SimpleNamespace(name="second"))  # no error, no second reconnect
    assert len(made) == 1 and ctx.tck_reconnects == 1


@pytest.mark.parametrize("message", [
    "<COMMUNICATION LINK ERROR> Connection closed",
    "<COMMUNICATION ERROR> Message out of order",
])
def test_both_spellings_are_a_lost_connection(message):
    from tests.tck.environment import _connection_lost

    assert _connection_lost(message)


@pytest.mark.parametrize("message", ["boom", "SQLCODE -124", "<SQL ERROR> constraint failed"])
def test_another_teardown_error_does_not_reconnect(monkeypatch, message):
    import tests.tck.environment as env

    ctx, _, _, made, _ = _context(monkeypatch, RuntimeError(message))
    with pytest.raises(env.TeardownFailed, match=message.split()[0]):
        env.after_scenario(ctx, SimpleNamespace(name="s"))
    assert made == [] and getattr(ctx, "tck_reconnects", 0) == 0
