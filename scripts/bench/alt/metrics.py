"""Turn the output of one suite run into ``{metric name: milliseconds}`` (spec 250).

Three sources, any combination per suite:

* ``BENCH_METRIC <name> = <ms>`` lines on stdout (wrappers print these; no other convention is needed);
* pytest ``--durations`` lines (``2.50s call tests/x.py::test``), one metric per test, in ms;
* numbers inside a JSON file the suite wrote, selected by a per-suite function.

Metric values are one number per rep; compare.py takes medians across reps.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_METRIC_RE = re.compile(r"^BENCH_METRIC\s+(.+?)\s*=\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*$")
_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)s\s+call\s+(\S+)")


def metrics_from_stdout(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        m = _METRIC_RE.match(line.strip())
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


def metrics_from_pytest_durations(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        m = _DURATION_RE.match(line)
        if m:
            out[m.group(2)] = round(float(m.group(1)) * 1000.0, 3)
    return out


def flatten_numbers(doc, include=None, prefix="") -> dict:
    """Dotted-path -> float for every numeric leaf (bools excluded); ``include`` filters on the leaf key name."""
    out = {}
    if isinstance(doc, dict):
        for k, v in doc.items():
            out.update(flatten_numbers(v, include, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            out.update(flatten_numbers(v, include, f"{prefix}.{i}" if prefix else str(i)))
    elif isinstance(doc, (int, float)) and not isinstance(doc, bool):
        leaf = prefix.rsplit(".", 1)[-1]
        if include is None or leaf in include:
            out[prefix] = float(doc)
    return out


def newest_json(outdir, pattern="*.json"):
    files = sorted(Path(outdir).glob(pattern), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def bench_py_json(outdir) -> dict:
    """tests/benchmarks/bench.py and bench_193_full.py: ``results`` is a list of per-(dataset, query, path) stats."""
    p = newest_json(outdir, "bench*.json")
    if not p:
        return {}
    doc = json.loads(p.read_text())
    out = {}
    for r in doc.get("results", []):
        key = ".".join(str(r.get(k)) for k in ("dataset", "query", "path") if r.get(k) is not None)
        for stat in ("hot_p50_ms", "hot_p90_ms"):
            if isinstance(r.get(stat), (int, float)):
                out[f"{key}.{stat}"] = float(r[stat])
    return out


def generic_json(outdir, include=None) -> dict:
    """Every numeric leaf of every JSON file in outdir (file stem is the prefix)."""
    out = {}
    for p in sorted(Path(outdir).glob("*.json")):
        try:
            doc = json.loads(p.read_text())
        except ValueError:
            continue
        out.update(flatten_numbers(doc, include, p.stem))
    return out
