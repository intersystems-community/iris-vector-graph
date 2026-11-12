"""4.7.0, live: scripts/dev/tcp_setup.run runs ObjectScript lines the way `iris session` with them on its input did.

Cortex XDR kills a process that pipes ObjectScript into `iris session`, so the test helpers that did (ns262._sys,
ns460alt.session, the doc harness and about 30 files; tests/unit/test_no_iris_session_pipes.py lists them) move to
this runner, which goes over TCP and the Native API. What those callers relied on, each pinned here against the known
answer written by hand:
- output in order, across lines, with `!` and blocks, and a line that leaves the cursor mid-line ending on a new line
  (the terminal's next prompt started one, and the docs' stated answers show it);
- a line that errors writes the error where it happened, as the terminal did (`<UNDEFINED> *x`), the next line still
  runs, and the failure is reported with its line number (and raised with check=True);
- Halt in a line ends the run there; a final `halt`, as every terminal script ends, is just the end of the lines;
- `zn` lasts for the lines after it, and the run starts in the namespace asked for;
- each run is a fresh process (locals and process-private globals do not carry over), as each session was;
- non-ASCII output comes back whole; a run past its timeout is ended and raises; %SYS only with allow_sys.

Where: IVG_TCPRUN_NS (the gate makes it empty, `tcprun`), on 127.0.0.1:IVG_PORT (default 31972). The lines write only
to the output, process-private globals and IRISTEMP.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
NS = os.environ.get("IVG_TCPRUN_NS", "")

pytestmark = [pytest.mark.e2e, pytest.mark.skipif(not NS, reason="IVG_TCPRUN_NS must name a scratch namespace")]

sys.path.insert(0, str(REPO / "scripts" / "dev"))
import tcp_setup  # noqa: E402


@pytest.fixture(scope="module")
def native():
    with tcp_setup.sys_native() as handle:
        yield handle


def test_output_comes_back_in_order(native):
    r = tcp_setup.run(['write "a",!', 'write "b"', 'write "c",!', "for i=1:1:3 { write i }", 'write !,"d",!'], NS,
                      native=native)
    # a line that leaves the cursor mid-line ends on a new line, as the terminal's next prompt started one
    assert r.output == "a\nb\nc\n123\n\nd\n"
    assert (r.state, r.failures) == ("ended", [])


def test_lines_without_a_newline_read_as_the_terminal_showed_them(native):
    """README's `write $data(...)` pair: the terminal showed 1 and 0 on two lines (the docs' stated answer)."""
    assert tcp_setup.run(["write 1", "write 0"], NS, native=native).output == "1\n0\n"


def test_a_failing_line_writes_its_error_and_the_next_line_runs(native):
    r = tcp_setup.run(['write "before",!', "kill x write x", 'write "after",!'], NS, native=native)
    assert r.output == "before\n<UNDEFINED> *x\nafter\n"
    assert r.failures == [(2, "<UNDEFINED> *x")]
    assert r.state == "ended"
    with pytest.raises(RuntimeError, match=r"line 2: <UNDEFINED> \*x"):
        tcp_setup.run(['write "before",!', "kill x write x"], NS, native=native, check=True)


def test_an_error_in_called_code_names_where(native):
    r = tcp_setup.run(["write $zdate(-5)"], NS, native=native)
    assert r.failures and r.failures[0][1].startswith("<"), r
    assert r.failures[0][1] in r.output


def test_halt_ends_the_run_there(native):
    r = tcp_setup.run(['write "one",!', 'write "two",! halt', 'write "three",!'], NS, native=native)
    assert r.output == "one\ntwo\n"
    assert r.state == "halted"


def test_a_final_halt_is_the_end_of_the_lines(native):
    r = tcp_setup.run(['write "one",!', "halt"], NS, native=native)
    assert (r.output, r.state) == ("one\n", "ended")


def test_zn_lasts_for_the_lines_after_it(native):
    r = tcp_setup.run(["write $namespace,!", 'zn "%SYS"', "write $namespace,!", f'zn "{NS}"', "write $namespace,!"],
                      NS, native=native)
    assert r.output == f"{NS}\n%SYS\n{NS}\n"


def test_each_run_is_a_fresh_process(native):
    first = tcp_setup.run(["set y=5,^||p=1", "write y,$data(^||p),!"], NS, native=native)
    assert first.output == "51\n"
    again = tcp_setup.run(["write $data(y),$data(^||p),!"], NS, native=native)
    assert again.output == "00\n"


def test_non_ascii_output_comes_back_whole(native):
    r = tcp_setup.run(['write "é ü 中 ✓",!'] * 3, NS, native=native)
    assert r.output == "é ü 中 ✓\n" * 3


def test_a_long_output_is_read_whole(native, monkeypatch):
    monkeypatch.setattr(tcp_setup, "RUN_CHUNK", 1000)
    r = tcp_setup.run(['for i=1:1:2000 { write i,"中",! }'], NS, native=native)
    assert r.output == "".join(f"{i}中\n" for i in range(1, 2001))


def test_a_run_past_its_timeout_is_ended(native):
    import re
    import time

    with pytest.raises(TimeoutError) as exc:
        tcp_setup.run(["set ^IRIS.TempIVGRunProbe($job)=1", "hang 30"], NS, native=native, timeout=2)
    pid = re.search(r"process (\d+) ended", str(exc.value)).group(1)
    try:
        assert native.get("^IRIS.TempIVGRunProbe", int(pid)) == 1  # it was that process that ran the lines
        for _ in range(50):
            if not int(native.classMethodValue("%SYS.ProcessQuery", "%ExistsId", pid)):
                break
            time.sleep(0.1)
        assert not int(native.classMethodValue("%SYS.ProcessQuery", "%ExistsId", pid)), f"process {pid} still runs"
    finally:
        native.kill("^IRIS.TempIVGRunProbe")


def test_sys_needs_allow_sys(native):
    with pytest.raises(ValueError, match="allow_sys"):
        tcp_setup.run(["write 1"], "%SYS", native=native)
    assert tcp_setup.run(["write $namespace"], "%SYS", native=native, allow_sys=True).output == "%SYS\n"


def test_session_is_the_text_and_nothing_ran_iris_session(native):
    before = _sessions()
    assert tcp_setup.session(['write "x",!', "halt"], NS, native=native) == "x\n"
    assert before == _sessions() == []


def _sessions() -> list:
    """`iris session` processes in the test container, as `docker top` lists them."""
    container = os.environ.get("IVG_TEST_CONTAINER", "ivg-iris-enterprise")
    out = subprocess.run(["docker", "top", container, "-o", "pid,args"], capture_output=True, text=True, timeout=30)
    return [line for line in out.stdout.splitlines() if "iris session" in line or "irissession" in line]
