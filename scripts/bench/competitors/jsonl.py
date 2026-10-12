"""The result record and the timing loop shared by every runner.

One JSON object per line:
    {"engine": "neo4j|memgraph|networkx|igraph", "suite": "<name>", "query": "<id>",
     "metric": "latency_ms", "values_ms": [...], "params": {...}}
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

KEYS = ("engine", "suite", "query", "metric", "values_ms", "params")


def make_record(engine: str, suite: str, query: str, values_ms: List[float], params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "engine": engine,
        "suite": suite,
        "query": query,
        "metric": "latency_ms",
        "values_ms": [round(float(v), 4) for v in values_ms],
        "params": dict(params or {}),
    }


def validate_record(rec: Dict[str, Any]) -> None:
    missing = [k for k in KEYS if k not in rec]
    if missing:
        raise ValueError(f"record is missing {missing}")
    if rec["metric"] != "latency_ms":
        raise ValueError("metric must be latency_ms")
    if not isinstance(rec["values_ms"], list) or not all(isinstance(v, (int, float)) for v in rec["values_ms"]):
        raise ValueError("values_ms must be a list of numbers")
    if not isinstance(rec["params"], dict):
        raise ValueError("params must be an object")


def write_record(path: Path, rec: Dict[str, Any]) -> None:
    validate_record(rec)
    with open(path, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def time_runs(
    fn: Callable[[], Any], warmup: int = 3, runs: int = 20, catch: bool = False
) -> Tuple[List[float], Any]:
    """Run fn `warmup` times untimed, then `runs` times timed with perf_counter. Returns (values_ms, last result).

    With catch=True an exception ends the leg: an empty list is returned (a partial set of timings from a leg that
    failed part way is not a measurement) and the exception is returned in place of the result. Without it the
    exception propagates.
    """
    last: Any = None
    try:
        for _ in range(warmup):
            fn()
        values: List[float] = []
        for _ in range(runs):
            t0 = time.perf_counter()
            last = fn()
            values.append((time.perf_counter() - t0) * 1000.0)
        return values, last
    except Exception as exc:
        if not catch:
            raise
        return [], exc
