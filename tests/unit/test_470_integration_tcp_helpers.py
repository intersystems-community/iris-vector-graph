"""4.7.0: tests/integration/_tcp.py, the TCP replacements for the integration files' `iris session` calls.

Offline: what `objectscript_string` writes for a text, and what `HeldLock` asks `tcp_setup.run` to do, in another
thread, and how its release waits for the holder and reports a holder that failed. The runner itself is pinned in
tests/unit/test_tcp_run.py and, live, tests/e2e/test_470_tcp_run_e2e.py; the lock as ObjectScript's Lock sees it is
what tests/integration/test_044_bm25_incremental.py and test_ledger_lock_and_chunks.py check against the server.
"""

from __future__ import annotations

import threading
import time

import pytest

from tests.integration import _tcp


@pytest.mark.parametrize("text, literal", [
    ("abc", '"abc"'),
    ('say "hi"', '"say ""hi"""'),
    ("a\nb", '"a"_$c(10)_"b"'),
    ("a\n", '"a"_$c(10)_""'),
    ("", '""'),
])
def test_objectscript_string_is_the_text_as_a_literal(text, literal):
    assert _tcp.objectscript_string(text) == literal


def test_session_is_tcp_setup_session():
    assert _tcp.session is _tcp.tcp_setup.session
    assert _tcp.tcp_setup.__file__.endswith("scripts/dev/tcp_setup.py")


class FakeRunner:
    """Stands in for tcp_setup.run: records the call, and blocks until `done` is set, as the hang does."""

    def __init__(self, failures=(), raises=None):
        self.calls = []
        self.started = threading.Event()
        self.done = threading.Event()
        self.failures, self.raises = list(failures), raises

    def __call__(self, lines, namespace, **kwargs):
        self.calls.append((list(lines), namespace, kwargs))
        self.started.set()
        self.done.wait(10)
        if self.raises:
            raise self.raises
        return _tcp.tcp_setup.RunResult("", "ended", self.failures)


def test_held_lock_runs_lock_and_hang_in_the_namespace_in_another_thread(monkeypatch):
    fake = FakeRunner()
    monkeypatch.setattr(_tcp.tcp_setup, "run", fake)
    monkeypatch.setattr(_tcp, "SETTLE_SECONDS", 0)
    held = _tcp.HeldLock('^BM25Idx("ix","lock")', "IVG470X", seconds=7)
    assert fake.started.wait(5)
    lines, namespace, kwargs = fake.calls[0]
    assert lines == ['lock +^BM25Idx("ix","lock")', "hang 7"]
    assert namespace == "IVG470X"
    assert kwargs["timeout"] > 7
    assert held.thread.is_alive()  # the constructor returned while the holder still holds
    fake.done.set()
    held.release()
    assert not held.thread.is_alive()


def test_release_waits_for_the_holder_to_end(monkeypatch):
    fake = FakeRunner()
    monkeypatch.setattr(_tcp.tcp_setup, "run", fake)
    monkeypatch.setattr(_tcp, "SETTLE_SECONDS", 0)
    held = _tcp.HeldLock("^X", "IVG470X", seconds=1)
    threading.Timer(0.3, fake.done.set).start()
    t0 = time.monotonic()
    held.release()
    assert time.monotonic() - t0 >= 0.25


def test_a_holder_whose_lock_line_failed_is_reported_on_release(monkeypatch):
    fake = FakeRunner(failures=[(1, "<LOCKED>")])
    fake.done.set()
    monkeypatch.setattr(_tcp.tcp_setup, "run", fake)
    monkeypatch.setattr(_tcp, "SETTLE_SECONDS", 0)
    held = _tcp.HeldLock("^X", "IVG470X", seconds=1)
    with pytest.raises(RuntimeError, match="LOCKED"):
        held.release()


def test_a_holder_that_raised_is_reported_on_release(monkeypatch):
    fake = FakeRunner(raises=RuntimeError("IVG470X: FAIL ns: namespace IVG470X does not exist"))
    fake.done.set()
    monkeypatch.setattr(_tcp.tcp_setup, "run", fake)
    monkeypatch.setattr(_tcp, "SETTLE_SECONDS", 0)
    held = _tcp.HeldLock("^X", "IVG470X", seconds=1)
    with pytest.raises(RuntimeError, match="does not exist"):
        held.release()
