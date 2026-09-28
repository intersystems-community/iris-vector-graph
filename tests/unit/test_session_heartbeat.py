"""A session connection left idle for five minutes is dead when the next test needs it.

Measured against `ivg-iris-enterprise` from the host: a connection that has run a
query and then sits idle is alive at 300 s and gone at 420 s, on both the OrbStack
address (`:1972`) and the published port (`localhost:31972`). The next call fails
with `EPIPE` / `ECONNRESET`. Nothing in the harness or the library closes it.

`arno_iris_connection` is session-scoped and only a few files use it. In the full
unit run it went unused from `test_bfs_arno` to `test_rrf_fuse_e2e`, about six
minutes, so every Enterprise test after that failed at setup:

    tests/conftest.py:845: in arno_master_cleanup
        arno_iris_connection.commit()
    E   iris.dbapi.OperationalError: <COMMUNICATION LINK ERROR> ... EPIPE

The same run passes when those files run on their own, and so does
`test_bfs_arno` + `test_rrf_fuse_e2e` with no sleep between them. The failure comes
back with a 420 s `time.sleep` between them in place of the other tests.

`SessionHeartbeat` runs `SELECT 1` on each registered session connection that has
gone `interval` seconds without one. It runs between tests, on the test thread, so
it never shares a connection with a running test.
"""

import logging

import pytest

from tests.conftest import SessionHeartbeat


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class _Cursor:
    def __init__(self, conn):
        self._conn = conn
        self.closed = False

    def execute(self, sql, params=None):
        if self._conn.dead:
            raise RuntimeError("<COMMUNICATION LINK ERROR> ... EPIPE")
        self._conn.statements.append(sql)

    def fetchall(self):
        return [(1,)]

    def close(self):
        self.closed = True


class _Conn:
    def __init__(self, dead=False):
        self.dead = dead
        self.statements = []
        self.cursors = []

    def cursor(self):
        cur = _Cursor(self)
        self.cursors.append(cur)
        return cur


def _beat(interval=120.0):
    clock = _Clock()
    return SessionHeartbeat(interval=interval, clock=clock), clock


def test_nothing_is_sent_inside_the_interval():
    hb, clock = _beat()
    conn = _Conn()
    hb.register("arno", conn)
    clock.now += 119
    hb.beat()
    assert conn.statements == []


def test_a_connection_idle_for_the_interval_is_pinged():
    hb, clock = _beat()
    conn = _Conn()
    hb.register("arno", conn)
    clock.now += 120
    hb.beat()
    assert conn.statements == ["SELECT 1"]
    assert all(c.closed for c in conn.cursors)


def test_a_ping_resets_the_interval():
    hb, clock = _beat()
    conn = _Conn()
    hb.register("arno", conn)
    clock.now += 130
    hb.beat()
    clock.now += 60
    hb.beat()
    assert conn.statements == ["SELECT 1"]
    clock.now += 60
    hb.beat()
    assert conn.statements == ["SELECT 1", "SELECT 1"]


def test_each_connection_keeps_its_own_interval():
    hb, clock = _beat()
    a, b = _Conn(), _Conn()
    hb.register("a", a)
    clock.now += 100
    hb.register("b", b)
    clock.now += 20
    hb.beat()
    assert a.statements == ["SELECT 1"]
    assert b.statements == []


def test_an_unregistered_connection_is_left_alone():
    hb, clock = _beat()
    conn = _Conn()
    hb.register("arno", conn)
    hb.unregister("arno")
    clock.now += 500
    hb.beat()
    assert conn.statements == []


def test_a_dead_connection_is_named_once_and_does_not_raise(caplog):
    hb, clock = _beat()
    conn = _Conn(dead=True)
    hb.register("arno_iris_connection", conn)
    clock.now += 120
    with caplog.at_level(logging.ERROR):
        hb.beat()
        clock.now += 120
        hb.beat()
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "arno_iris_connection" in errors[0].getMessage()
    assert all(c.closed for c in conn.cursors)


def test_the_default_interval_is_well_inside_the_measured_drop():
    assert SessionHeartbeat().interval <= 150


@pytest.mark.parametrize("fixture_name", ["iris_connection", "arno_iris_connection"])
def test_both_session_connections_register(fixture_name):
    import inspect

    import tests.conftest as conftest

    src = inspect.getsource(getattr(conftest, fixture_name).__wrapped__)
    assert f'_SESSION_HEARTBEAT.register("{fixture_name}", conn)' in src
    assert f'_SESSION_HEARTBEAT.unregister("{fixture_name}")' in src
