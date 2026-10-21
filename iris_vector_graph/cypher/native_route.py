"""Which path answers a query shape natively planned: native, or SQL where SQL measures faster (spec 253).

Both paths give the same answer (the shadow gate, FR-010), so the choice is speed alone. Native is timed first. A shape
whose native median is under `small_ms` is then timed on SQL as many times, and the path with the lower median keeps
it; one native answers in `small_ms` or more never runs on SQL (its SQL run could take seconds: BI11 is 1.7 s natively,
26 s on SQL), nor one whose first timed SQL run takes `slower` times native's median. Each path's first run is a warm-up
and is not counted: a statement's first SQL run compiles it.
"""

from __future__ import annotations

import statistics
from typing import Dict, List, Optional


class NativeRoute:
    def __init__(self, small_ms: float, trials: int = 3, slower: float = 4.0):
        self.small_ms = small_ms
        self.trials = trials
        self.slower = slower
        self.choice: Optional[str] = None
        self._runs = {"native": 0, "sql": 0}
        self._ms: Dict[str, List[float]] = {"native": [], "sql": []}

    def next_path(self) -> str:
        if self.choice is not None:
            return self.choice
        return "native" if len(self._ms["native"]) < self.trials else "sql"

    def record(self, path: str, ms: float) -> None:
        if self.choice is not None:
            return
        self._runs[path] += 1
        if self._runs[path] == 1:
            return  # the warm-up
        timed = self._ms[path]
        timed.append(ms)
        if path == "native":
            if len(timed) >= self.trials and statistics.median(timed) >= self.small_ms:
                self.choice = "native"
            return
        native = statistics.median(self._ms["native"])
        if ms > self.slower * native:
            self.choice = "native"
        elif len(timed) >= self.trials:
            self.choice = "sql" if statistics.median(timed) < native else "native"
