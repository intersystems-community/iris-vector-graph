"""The tests' TCP stand-in for `docker exec -i <container> iris session IRIS -U <namespace>` with lines on its input.

Cortex XDR kills a process that pipes ObjectScript into `iris session`, so test code runs its ObjectScript lines
through scripts/dev/tcp_setup.py's `run` / `session` instead: over TCP on 127.0.0.1:IVG_PORT, in a fresh process, one
line at a time, each error written where it happened (`<UNDEFINED> *x`) and the next line run, Halt ending the run, a
final `halt` being the end of the lines. The text has no banner, prompts or echoed commands. %SYS needs allow_sys=True.
"""

from __future__ import annotations

import sys
from pathlib import Path

_DEV = str(Path(__file__).resolve().parents[2] / "scripts" / "dev")
if _DEV not in sys.path:
    sys.path.insert(0, _DEV)

import tcp_setup  # noqa: E402

RunResult = tcp_setup.RunResult
run = tcp_setup.run
session = tcp_setup.session

__all__ = ["RunResult", "run", "session", "tcp_setup"]
