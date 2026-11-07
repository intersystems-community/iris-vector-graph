"""Which path answers a query shape natively planned: native, or SQL where SQL measures faster (spec 253).

Both paths give the same answer (the shadow gate, FR-010), so the choice is speed alone, settled by a trial:

- Native is timed first: a warm-up and two timed runs. A shape whose native median is `small_ms` or more never runs on
  SQL (its SQL run could take seconds: BI11 is 1.7 s natively, 26 s on SQL).
- Then SQL's warm-up (a statement's first SQL run compiles it), and the two paths' timed runs interleaved, so a passing
  disturbance (a busy host, a cache filling) falls on both, until each has `trials` timed runs. A timed SQL run of
  `slower` times native's slowest ends the trial at once, on native.
- The path that holds the shape (native, until a trial moves it) keeps it unless the other's median is at least `margin`
  lower: a near tie does not move it.

The trial runs again after `recheck` calls, and sooner when the held path's median over its last `window` calls drifts
to `drift` times its trial median: the first trial's runs can be unrepresentative (4.6.0's speed gate: three runs a path
put cypher_one_hop on SQL, at twice native's time). A failed SQL run gives the shape to native for good.
"""

from __future__ import annotations

import statistics
from collections import deque
from typing import Deque, Dict, List, Optional

PATHS = ("native", "sql")


class NativeRoute:
    #: native's timed runs before SQL is tried (and before `small_ms` is judged)
    NATIVE_FIRST = 2
    #: calls on the held path before its drift is judged, so a short burst of costly parameters does not start a trial
    DRIFT_AFTER = 50

    def __init__(
        self,
        small_ms: float,
        trials: int = 5,
        slower: float = 4.0,
        margin: float = 0.2,
        recheck: Optional[int] = 1000,
        drift: Optional[float] = 2.0,
        window: int = 20,
    ):
        self.small_ms = small_ms
        self.trials = trials
        self.slower = slower
        self.margin = margin
        self.recheck = recheck
        self.drift = drift
        self.window = window
        #: the path answering between trials; None until the first trial ends
        self.choice: Optional[str] = None
        self._held = "native"
        self._sql_failed = False
        self._trial_ms: Optional[float] = None
        self._start_trial()

    def _start_trial(self) -> None:
        self._trial = True
        self._runs: Dict[str, int] = {p: 0 for p in PATHS}
        self._ms: Dict[str, List[float]] = {p: [] for p in PATHS}
        self._last: Optional[str] = None

    def _decide(self, path: str) -> None:
        self.choice = self._held = path
        self._trial = False
        timed = self._ms[path]
        self._trial_ms = statistics.median(timed) if timed else None
        self._since = 0
        self._recent: Deque[float] = deque(maxlen=self.window)

    def sql_failed(self) -> None:
        """SQL failed where native answers this shape: native keeps it, and no later trial times SQL."""
        self._sql_failed = True
        self._decide("native")

    def next_path(self) -> str:
        if not self._trial:
            return self.choice
        if len(self._ms["native"]) < self.NATIVE_FIRST:
            return "native"
        if self._runs["sql"] == 0:
            return "sql"  # its warm-up
        return "native" if self._last == "sql" else "sql"

    def record(self, path: str, ms: float) -> None:
        if not self._trial:
            self._held_run(path, ms)
            return
        self._last = path
        self._runs[path] += 1
        if self._runs[path] == 1:
            return  # the warm-up
        self._ms[path].append(ms)
        native, sql = self._ms["native"], self._ms["sql"]
        if path == "native" and self._runs["sql"] == 0:
            if len(native) >= self.NATIVE_FIRST and statistics.median(native) >= self.small_ms:
                self._decide(self._held)  # slow natively: SQL is not tried
            return
        # one SQL run is compared with native's slowest: the runs see different parameters, and an answer's size can
        # vary 20x between them (BI04's countries)
        if path == "sql" and ms > self.slower * max(native):
            self._decide("native")
            return
        if len(native) >= self.trials and len(sql) >= self.trials:
            other = "sql" if self._held == "native" else "native"
            held_ms, other_ms = statistics.median(self._ms[self._held]), statistics.median(self._ms[other])
            self._decide(other if other_ms < held_ms * (1 - self.margin) else self._held)

    def _held_run(self, path: str, ms: float) -> None:
        if path != self.choice or self._sql_failed:
            return
        self._since += 1
        self._recent.append(ms)
        if self.recheck and self._since >= self.recheck:
            self._start_trial()
        elif (
            self.drift
            and self._trial_ms
            and self._since >= self.DRIFT_AFTER
            and len(self._recent) == self.window
            and statistics.median(self._recent) > self.drift * self._trial_ms
        ):
            self._start_trial()
