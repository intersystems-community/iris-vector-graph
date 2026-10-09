"""IRISGraphEngine._iris_obj opens its dedicated Native API connection as the caller, never as _SYSTEM.

Found by the least-privilege spike (docs/research/least-privilege-spike.md): the second connection
used a hard-coded `_SYSTEM` / `SYS`, so the BM25, vector and ledger calls ran with full rights
whatever role the caller had connected as.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from iris_vector_graph.engine import IRISGraphEngine


def _engine(params, conn):
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng.conn = conn
    eng._native_conn = None
    eng._connection_params = params
    return eng


def _conn(**kw):
    base = dict(hostname="db.example", port=1972, namespace="APP")
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def seams(monkeypatch):
    import iris
    import iris.dbapi as dbapi

    connect = MagicMock(name="connect", return_value=SimpleNamespace(isClosed=lambda: False))
    create = MagicMock(name="createIRIS", return_value="native-handle")
    monkeypatch.setattr(dbapi, "connect", connect)
    monkeypatch.setattr(iris, "createIRIS", create)
    return connect, create


def test_known_credentials_are_the_callers_own(seams):
    connect, create = seams
    eng = _engine({"hostname": "db.example", "port": 1972, "namespace": "APP", "username": "mindwalk_ro", "password": "s3cret"}, _conn())
    assert eng._iris_obj() == "native-handle"
    connect.assert_called_once_with(hostname="db.example", port=1972, namespace="APP", username="mindwalk_ro", password="s3cret")
    create.assert_called_once_with(connect.return_value)


def test_the_dedicated_connection_is_opened_once(seams):
    connect, _ = seams
    eng = _engine({"hostname": "h", "port": 1, "namespace": "N", "username": "u", "password": "p"}, _conn())
    eng._iris_obj()
    eng._iris_obj()
    assert connect.call_count == 1


def test_unknown_credentials_are_never_invented(seams):
    connect, create = seams
    eng = _engine(None, _conn())
    assert eng._iris_obj() == "native-handle"
    connect.assert_not_called()  # no _SYSTEM/SYS guess: the caller's own connection carries its role
    create.assert_called_once_with(eng.conn)


def test_params_without_a_username_are_treated_as_unknown(seams):
    connect, create = seams
    eng = _engine({"hostname": "h", "port": 1, "namespace": "N"}, _conn())
    eng._iris_obj()
    connect.assert_not_called()
    create.assert_called_once_with(eng.conn)


def test_a_failed_dedicated_connection_falls_back_to_the_callers_connection(seams):
    connect, create = seams
    connect.side_effect = RuntimeError("refused")
    eng = _engine({"hostname": "h", "port": 1, "namespace": "N", "username": "u", "password": "p"}, _conn())
    assert eng._iris_obj() == "native-handle"
    create.assert_called_once_with(eng.conn)


def test_the_engine_source_no_longer_carries_default_credentials():
    import inspect

    source = inspect.getsource(IRISGraphEngine._iris_obj)
    assert "_SYSTEM" not in source and '"SYS"' not in source
