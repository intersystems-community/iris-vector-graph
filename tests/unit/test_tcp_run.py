"""4.7.0: `tcp_setup.run` / `tcp_setup.session` run ObjectScript lines in a namespace over TCP, as `iris session` did.

Offline, the Python side: what it stores, what it waits for, what it returns, and that it always cleans up. A fake
Native API handle answers IVGDev.Run's methods (checked against scripts/dev/objectscript/IVGDev/Run.cls) from a
script the test gives it. What the lines do inside IRIS (errors, Halt, ZN, order, a fresh process each run) is pinned
live, in tests/e2e/test_470_tcp_run_e2e.py.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from tests.helpers.fake_setup_iris import RUN_CLASS, class_methods

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "dev" / "tcp_setup.py"


@pytest.fixture(scope="module")
def ts():
    spec = importlib.util.spec_from_file_location("tcp_setup_run_470", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up there
    spec.loader.exec_module(module)
    return module


class FakeRun:
    """IVGDev.Run as the runner sees it. `script(lines)` answers (output, final state, {line: error})."""

    def __init__(self, script, polls_running=2, start="4242", setup_error=""):
        self.script, self.polls_running, self.start, self.setup_error = script, polls_running, start, setup_error
        self.globals: dict = {}
        self.calls: list = []
        self.methods = class_methods(RUN_CLASS)
        self.cleaned = []
        self.chunk = None

    def set(self, value, name, *subs):
        self.globals[(name, *subs)] = value

    def kill(self, name, *subs):
        for key in [k for k in self.globals if k[: 1 + len(subs)] == (name, *subs)]:
            del self.globals[key]

    def _lines(self, run_id):
        keys = sorted(k for k in self.globals if k[:3] == ("^IRIS.TempIVGRun", run_id, "line"))
        assert [k[3] for k in keys] == list(range(1, len(keys) + 1)), keys
        return [self.globals[k] for k in keys]

    def classMethodValue(self, cls, method, *args):
        assert cls == "IVGDev.Run", (cls, method)
        assert method in self.methods, f"IVGDev.Run has no class method {method}"
        assert len(args) <= self.methods[method], (method, args)
        self.calls.append((method, *args))
        run_id = args[0]
        if method == "Start":
            self.lines = self._lines(run_id)
            self.output, self.final, self.failures = self.script(self.lines)
            return self.start
        if method == "State":
            if self.polls_running:
                self.polls_running -= 1
                return "running"
            return self.final
        if method == "Output":
            start, size = args[1], args[2]
            self.chunk = size
            # as the Native API hands back a binary string: one character per byte
            return self.output.encode("utf-8")[start - 1: start - 1 + size].decode("latin-1")
        if method == "Failures":
            return "\n".join(f"{n}\t{e}" for n, e in self.failures.items())
        if method == "SetupError":
            return self.setup_error
        if method == "Cleanup":
            self.cleaned.append(run_id)
            self.kill("^IRIS.TempIVGRun", run_id)
            return ""
        raise AssertionError(method)


@pytest.fixture(autouse=True)
def no_sleep(ts, monkeypatch):
    monkeypatch.setattr(ts.time, "sleep", lambda s: None)


def test_every_runner_method_the_module_calls_is_in_the_class():
    import re

    called = set(re.findall(r'RUNNER,\s*"(\w+)"', SCRIPT.read_text()))
    assert {"Start", "State", "Output", "Failures", "SetupError", "Cleanup"} <= called
    assert called <= set(class_methods(RUN_CLASS)), called - set(class_methods(RUN_CLASS))


def echo(lines):
    return "".join(f"{line}\n" for line in lines), "ended", {}


def test_lines_are_stored_in_order_and_the_output_returned(ts):
    native = FakeRun(echo)
    result = ts.run(['write "a",!', 'write "b",!'], "IVG470RUN", native=native)
    assert native.lines == ['write "a",!', 'write "b",!']
    assert result.output == 'write "a",!\nwrite "b",!\n'
    assert result.state == "ended" and result.failures == []
    assert str(result) == result.output


def test_a_string_of_lines_is_split(ts):
    native = FakeRun(echo)
    ts.run('write 1\nwrite 2\n', "IVG470RUN", native=native)
    assert native.lines == ["write 1", "write 2"]


@pytest.mark.parametrize("last", ["halt", "HALT", "h", "H", " halt ", "Halt"])
def test_a_final_halt_is_the_end_of_input_not_a_halt(ts, last):
    """Callers end their scripts with `halt`, as the terminal needed; that is the end of the lines, so the run ends."""
    native = FakeRun(echo)
    result = ts.run(["write 1", last], "IVG470RUN", native=native)
    assert native.lines == ["write 1"]
    assert result.state == "ended"


def test_a_halt_inside_the_lines_is_kept(ts):
    native = FakeRun(lambda lines: ("1\n", "halted", {}))
    result = ts.run(["write 1,!", "halt", "write 2,!"], "IVG470RUN", native=native)
    assert native.lines == ["write 1,!", "halt", "write 2,!"]
    assert result.state == "halted"


def test_failures_are_returned_with_their_line(ts):
    native = FakeRun(lambda lines: ("\n<UNDEFINED>zX+1 *x\n", "ended", {2: "<UNDEFINED> *x"}))
    result = ts.run(["set y=1", "write x"], "IVG470RUN", native=native)
    assert result.failures == [(2, "<UNDEFINED> *x")]
    assert "<UNDEFINED>" in result.output


def test_check_raises_on_a_failed_line(ts):
    native = FakeRun(lambda lines: ("", "ended", {1: "<SYNTAX>"}))
    with pytest.raises(RuntimeError, match=r"line 1.*<SYNTAX>"):
        ts.run(["write ("], "IVG470RUN", native=native, check=True)
    assert native.cleaned


def test_the_output_is_read_in_chunks(ts, monkeypatch):
    big = "x" * 25 + "\n" + "y" * 30
    native = FakeRun(lambda lines: (big, "ended", {}))
    monkeypatch.setattr(ts, "RUN_CHUNK", 10)
    assert ts.run(["write"], "IVG470RUN", native=native).output == big
    assert native.chunk == 10


def test_chunks_are_bytes_so_a_character_is_never_split_or_repeated(ts, monkeypatch):
    """Measured live: reading UTF-8 output by character offset repeated '中'. Offsets are bytes; the text is decoded
    once, at the end."""
    text = "é ü 中\n" * 3
    native = FakeRun(lambda lines: (text, "ended", {}))
    monkeypatch.setattr(ts, "RUN_CHUNK", 2)
    assert ts.run(["write"], "IVG470RUN", native=native).output == text


def test_a_timeout_ends_the_process_and_raises(ts, monkeypatch):
    clock = iter(range(0, 10_000, 5))
    monkeypatch.setattr(ts.time, "monotonic", lambda: next(clock))
    native = FakeRun(echo, polls_running=10**6)
    with pytest.raises(TimeoutError, match="IVG470RUN"):
        ts.run(["hang 100"], "IVG470RUN", native=native, timeout=30)
    assert native.cleaned  # Cleanup terminates the process


def test_cleanup_runs_after_every_run(ts):
    native = FakeRun(echo)
    ts.run(["write 1"], "IVG470RUN", native=native)
    assert len(native.cleaned) == 1
    assert not native.globals


def test_a_refused_start_raises(ts):
    native = FakeRun(echo, start="FAIL ns: namespace NOPE does not exist")
    with pytest.raises(RuntimeError, match="NOPE does not exist"):
        ts.run(["write 1"], "NOPE", native=native)
    assert native.cleaned


def test_a_setup_error_raises(ts):
    native = FakeRun(lambda lines: ("", "halted", {}), setup_error="could not open /usr/irissys/mgr/Temp/x")
    with pytest.raises(RuntimeError, match="could not open"):
        ts.run(["write 1"], "IVG470RUN", native=native)


def test_sys_is_refused_unless_allowed(ts):
    native = FakeRun(echo)
    with pytest.raises(ValueError, match="allow_sys"):
        ts.run(["write 1"], "%SYS", native=native)
    with pytest.raises(ValueError, match="allow_sys"):
        ts.run(["write 1"], "%sys", native=native)
    assert native.calls == []
    ts.run(["write 1"], "%SYS", native=native, allow_sys=True)
    assert native.calls[0][:4] == ("Start", native.calls[0][1], "%SYS", 1)


def test_the_run_id_is_lower_case_alphanumeric(ts):
    """Run.cls refuses anything else (it names the output file)."""
    native = FakeRun(echo)
    ts.run(["write 1"], "IVG470RUN", native=native)
    run_id = native.calls[0][1]
    assert run_id.isalnum() and run_id == run_id.lower()


def test_session_is_the_text(ts, monkeypatch):
    """The drop-in for `subprocess.run([... "iris", "session" ...]).stdout`: the text, errors inline."""
    native = FakeRun(lambda lines: ("a\n\n<UNDEFINED> *x\n", "ended", {2: "<UNDEFINED> *x"}))
    assert ts.session(["write \"a\",!", "write x", "halt"], "IVG470RUN", native=native) == "a\n\n<UNDEFINED> *x\n"
