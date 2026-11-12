"""ObjectScript lines run over TCP for the integration files, which ran them through `iris session` until 4.7.0.

Cortex XDR kills any process that pipes ObjectScript into `iris session` (through `docker exec` or ssh), so these
files run their lines with scripts/dev/tcp_setup.py instead: `session(lines, namespace)` is the text the lines wrote,
with no banner, prompt or echo (tcp_setup.run's docstring has the rest). Connection: 127.0.0.1:IVG_PORT.

- `objectscript_string(text)`: `text` as an ObjectScript string literal, for a line that writes it into a stream.
- `HeldLock(ref, namespace, seconds)`: an ObjectScript process holds `Lock +<ref>` for `seconds`, in another thread,
  as a terminal session started in the background did. A Native API lock is not seen by ObjectScript's Lock command,
  so the holder has to be an ObjectScript process.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Optional

_DEV = str(Path(__file__).resolve().parents[2] / "scripts" / "dev")
if _DEV not in sys.path:
    sys.path.insert(0, _DEV)
import tcp_setup  # noqa: E402  (scripts/dev/tcp_setup.py)

session = tcp_setup.session

#: How long the constructor waits for the holder's process to take the lock (the terminal holders waited 3 s).
SETTLE_SECONDS = 3


def objectscript_string(text: str) -> str:
    """`text` as an ObjectScript string literal: quotes doubled, each newline `_$c(10)_`."""
    return "_$c(10)_".join('"' + part.replace('"', '""') + '"' for part in text.split("\n"))


class HeldLock:
    """`Lock +<ref>` held by an ObjectScript process in `namespace` for `seconds` (a thread waits for it).

    `release()` waits until the holder has ended, which is when the lock is gone, and raises when the holder's run
    failed (its lock line erred, or the run could not start), so a test never reads a lock nobody held as a pass."""

    def __init__(self, ref: str, namespace: str, seconds: float):
        self.lines = [f"lock +{ref}", f"hang {seconds}"]
        self.namespace = namespace
        self.result: Optional[tcp_setup.RunResult] = None
        self.error: Optional[BaseException] = None
        self.thread = threading.Thread(target=self._hold, args=(seconds,), daemon=True)
        self.thread.start()
        time.sleep(SETTLE_SECONDS)

    def _hold(self, seconds: float) -> None:
        try:
            self.result = tcp_setup.run(self.lines, self.namespace, timeout=seconds + 120)
        except BaseException as exc:  # handed to release(), which raises it in the test's thread
            self.error = exc

    def release(self) -> None:
        self.thread.join()
        if self.error is not None:
            raise RuntimeError(f"the lock holder in {self.namespace} failed: {self.error}") from self.error
        if self.result is not None and self.result.failures:
            raise RuntimeError(f"the lock holder in {self.namespace} failed: {self.result.failures}")
