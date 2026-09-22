"""The session's dedicated native connection has to survive its own death.

`tests/conftest.py` redirects every `iris.createIRIS(session_connection)` call to
one dedicated native connection, opened lazily and then reused for the whole
session. That sharing is deliberate — `createIRIS` on a connection that also runs
cursor DDL corrupts the driver's parameter binding — but until 4.0.0 nothing
noticed when the dedicated connection died. The T074 gate showed what that costs:

    tests/e2e/test_upgrade_migrate_globals_e2e.py  EEEEEEEEEEEEEEEEEEEEEEEEEEEEEE
    tests/e2e/test_upgrade_snapshot_restore_e2e.py EEEEEEEEEEEEEEEEEEEEEEEEEEEEE…

    tests/conftest.py:621: in iris_master_cleanup
        _iris.createIRIS(iris_connection).classMethodValue(
    E   RuntimeError: <COMMUNICATION LINK ERROR> Failed to send message;
        Details: Error code: 32 … EPIPE.  Reason: (32) Broken pipe

69 errors, one dead socket. Every one of them was a test that never ran, reported
as a failure of the test rather than of the harness, and the single real event —
whatever killed the connection — was buried in the 69th copy of its own message.

So the proxy here reconnects once and says so at `ERROR`. It does not make the
death invisible: it makes it singular and attributable to the test that caused it.
"""

import pytest

from tests.conftest import (
    ReconnectingNative,
    native_error_means_the_connection_is_gone,
)


class _FakeNative:
    """Stands in for the object `iris.createIRIS()` returns."""

    def __init__(self, generation, raise_on_first=None):
        self.generation = generation
        self._raise_on_first = raise_on_first
        self.calls = []

    def classMethodValue(self, cls, method, *args):
        self.calls.append((cls, method, args))
        if self._raise_on_first is not None:
            exc = self._raise_on_first
            self._raise_on_first = None
            raise exc
        return f"gen{self.generation}:{cls}.{method}"


EPIPE_MESSAGE = (
    "<COMMUNICATION LINK ERROR> Failed to send message; Details: Error code: 32 "
    "Error message: write_all:  send() returned error EPIPE.  Reason: (32) Broken pipe"
)


def test_the_gates_own_epipe_text_is_recognised_as_a_dead_connection():
    # Verbatim from the T074 gate. A marker list that does not match the message
    # the gate actually produced would reconnect on nothing.
    assert native_error_means_the_connection_is_gone(RuntimeError(EPIPE_MESSAGE))
    assert native_error_means_the_connection_is_gone(
        RuntimeError("<COMMUNICATION LINK ERROR> Connection closed")
    )
    assert native_error_means_the_connection_is_gone(
        RuntimeError("<COMMUNICATION ERROR> Message out of order")
    )


def test_an_ordinary_error_is_not_a_dead_connection():
    # An ObjectScript error has to reach the test unchanged, and must not spend a
    # reconnect: retrying it would run the same failing method twice.
    assert not native_error_means_the_connection_is_gone(
        RuntimeError("<UNDEFINED>zEraseAll+4^Graph.KG.Eraser.1 *tSC")
    )
    assert not native_error_means_the_connection_is_gone(ValueError("no"))


def test_a_dead_connection_is_reopened_once_and_the_call_answers():
    opened = []

    def _open():
        gen = len(opened) + 1
        native = _FakeNative(gen, raise_on_first=RuntimeError(EPIPE_MESSAGE) if gen == 1 else None)
        opened.append(native)
        return native

    reconnects = []
    proxy = ReconnectingNative(_open, on_reconnect=reconnects.append)

    assert proxy.classMethodValue("Graph.KG.Eraser", "EraseAll") == (
        "gen2:Graph.KG.Eraser.EraseAll"
    )
    assert len(opened) == 2, "the dead connection was not replaced"
    assert len(reconnects) == 1, "the reconnect went unreported"
    assert EPIPE_MESSAGE in str(reconnects[0])


def test_a_healthy_connection_is_not_reopened():
    opened = []

    def _open():
        native = _FakeNative(len(opened) + 1)
        opened.append(native)
        return native

    proxy = ReconnectingNative(_open)
    for _ in range(3):
        proxy.classMethodValue("%SYSTEM.Version", "GetNumber")

    assert len(opened) == 1, "a live connection was replaced anyway"
    assert len(opened[0].calls) == 3


def test_an_ordinary_error_propagates_without_a_reconnect():
    opened = []

    def _open():
        native = _FakeNative(
            len(opened) + 1, raise_on_first=RuntimeError("<UNDEFINED>zEraseAll+4")
        )
        opened.append(native)
        return native

    proxy = ReconnectingNative(_open)
    with pytest.raises(RuntimeError, match="UNDEFINED"):
        proxy.classMethodValue("Graph.KG.Eraser", "EraseAll")

    assert len(opened) == 1


def test_the_retry_happens_once_not_forever():
    # If the second connection is dead too, the harness has to stop and say so
    # rather than reopen a socket per call for the rest of the session.
    opened = []

    def _open():
        native = _FakeNative(len(opened) + 1, raise_on_first=RuntimeError(EPIPE_MESSAGE))
        opened.append(native)
        return native

    proxy = ReconnectingNative(_open)
    with pytest.raises(RuntimeError, match="EPIPE"):
        proxy.classMethodValue("Graph.KG.Eraser", "EraseAll")

    assert len(opened) == 2, "expected exactly one reconnect attempt"


def test_a_non_callable_attribute_still_reads_through():
    proxy = ReconnectingNative(lambda: _FakeNative(1))
    assert proxy.generation == 1
