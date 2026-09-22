"""Spec 230 US5 — the harness proves which instance answered.

On 2026-09-18 a whole suite ran against `irispython-dx-iris` on `localhost:1972`
and reported `547 passed`. The only signal was two `<CLASS DOES NOT EXIST>
Graph.KG.Traversal` failures. A green run against the wrong database is not a
partial measurement; it is no measurement, and every gate number in the 4.0.0
release depends on this not happening again.

`container_state_is_running` already stops the *stopped-container* version of the
mistake. It cannot stop this one: a container can be running and healthy while the
connection the fixture hands out reaches something else entirely — an SSH tunnel on
the same port, another project's container, a stray `iris` on the host.

What makes a positive assertion possible is that the instance can be asked its own
hostname, and Docker knows what that hostname should be. Measured on this build
(2026-09-21):

- `SELECT $SYSTEM.INetInfo.LocalHostName()`, `SELECT $ZU(110)` and
  `SELECT $SYSTEM.Util.InstallDirectory()` all fail with `SQLCODE -12 <A term
  expected...>`. A `$SYSTEM` reference is not a valid SQL term here, so there is
  no SQL route.
- `CALL %SYSTEM.INetInfo_LocalHostName()` and `SELECT TOP 1 * FROM
  %SYS.ProcessQuery WHERE Pid = $JOB` both hung past 120 seconds.
  `%SYS.ProcessQuery` blocks; it cannot go in a fixture.
- The Native API works: `classMethodValue("%SYSTEM.INetInfo", "LocalHostName")`
  returned `'d16bb70c311d'`, exactly `docker inspect -f
  '{{.Config.Hostname}}' ivg-iris-enterprise`. It needs no write and no user
  table, so it works on all four of the fixture's fallback paths.
"""

from __future__ import annotations

import pytest

from tests.conftest import container_hostname_matches


# ---------------------------------------------------------------------------
# The pure comparison
# ---------------------------------------------------------------------------

_FULL_ID = "d16bb70c311d9f2b8a4e5c7d1f3a6b9c2e4d8f0a1b3c5d7e9f2a4b6c8d0e2f4a6"
_HOSTNAME = "d16bb70c311d"


def test_a_twelve_character_hostname_matches_its_container_id():
    """What the instance reports is the first twelve characters of `{{.Id}}`."""
    assert container_hostname_matches(_HOSTNAME, container_id=_FULL_ID) is True


def test_the_declared_hostname_matches_exactly():
    """A container started with `--hostname` reports that name, not an ID prefix.

    `{{.Config.Hostname}}` is authoritative for whatever the hostname actually is,
    so an exact match against it is the primary comparison and the ID prefix is the
    fallback.
    """
    assert container_hostname_matches("ivg-box", expected_hostname="ivg-box") is True
    assert container_hostname_matches("ivg-box", expected_hostname="other-box") is False


def test_a_different_instance_does_not_match():
    """The failure this gate exists for: another container answered."""
    assert container_hostname_matches("a1b2c3d4e5f6", container_id=_FULL_ID) is False


@pytest.mark.parametrize("reported", ["", "   ", None, 0, False])
def test_an_unreadable_hostname_fails_closed(reported):
    """A probe that could not read a hostname has proven nothing.

    This is the direction that matters. A helper that returned True on an empty
    reading would turn the whole assertion into a no-op on exactly the builds where
    the probe does not work — which is how the original mistake stayed invisible.
    """
    assert container_hostname_matches(reported, expected_hostname=_HOSTNAME,
                                      container_id=_FULL_ID) is False


def test_no_expectation_at_all_fails_closed():
    """Nothing to compare against is not a pass."""
    assert container_hostname_matches(_HOSTNAME) is False
    assert container_hostname_matches(_HOSTNAME, expected_hostname="", container_id="") is False


def test_case_and_whitespace_are_normalised():
    """Docker's output carries a trailing newline; `container_state_is_running`
    strips and lowercases for the same reason."""
    assert container_hostname_matches(f"  {_HOSTNAME.upper()}\n",
                                      container_id=_FULL_ID) is True


def test_a_short_prefix_does_not_match_by_accident():
    """A one-character hostname is a prefix of nearly every ID.

    Accepting it would make the gate pass against an arbitrary instance whose
    hostname happens to start with the same nibble.
    """
    assert container_hostname_matches("d", container_id=_FULL_ID) is False
    assert container_hostname_matches("d16bb70c311", container_id=_FULL_ID) is False


# ---------------------------------------------------------------------------
# The probe uses its own connection
# ---------------------------------------------------------------------------


class _FakeNative:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeConn:
    hostname = "10.0.0.5"
    port = 1972
    namespace = "USER"


def test_the_probe_opens_and_closes_its_own_connection(monkeypatch):
    """`tests/conftest.py` records why this matters (see its `_safe_createIRIS`
    comment): `iris.createIRIS(conn)` plus cursor DDL on the same connection
    permanently corrupts the IRIS Python driver's parameter binding state. The whole
    monkeypatch exists to keep the native API off the session connection.

    A probe that reused the connection under test would corrupt the session it was
    supposed to be validating — and it would do so on every run, not just the wrong
    ones.
    """
    import iris as _iris

    from tests import conftest as _conftest

    under_test = _FakeConn()
    opened: list = []

    def _fake_connect(**kwargs):
        native = _FakeNative()
        opened.append((native, kwargs))
        return native

    def _fake_createIRIS(target):
        assert target is not under_test, (
            "the probe handed the connection under test to createIRIS — that "
            "corrupts the driver's parameter binding for the rest of the session"
        )

        class _IRIS:
            @staticmethod
            def classMethodValue(cls_name, method):
                assert (cls_name, method) == ("%SYSTEM.INetInfo", "LocalHostName")
                return _HOSTNAME

        return _IRIS()

    monkeypatch.setattr(_iris, "connect", _fake_connect)
    monkeypatch.setattr(_iris, "createIRIS", _fake_createIRIS)

    assert _conftest.probe_instance_hostname(under_test) == _HOSTNAME
    assert len(opened) == 1, "the probe must open exactly one handle of its own"
    native, kwargs = opened[0]
    assert native.closed, "the probe must close the handle it opened"
    assert kwargs["hostname"] == under_test.hostname
    assert kwargs["port"] == under_test.port
    assert kwargs["namespace"] == under_test.namespace


def test_the_probe_returns_none_when_it_cannot_ask(monkeypatch):
    """A probe that raises would fail the session for the wrong reason.

    It returns `None`, and the caller decides — which it does by failing, because
    an unreadable hostname fails closed above.
    """
    import iris as _iris

    from tests import conftest as _conftest

    def _boom(**kwargs):
        raise RuntimeError("no native API on this build")

    monkeypatch.setattr(_iris, "connect", _boom)
    assert _conftest.probe_instance_hostname(_FakeConn()) is None
