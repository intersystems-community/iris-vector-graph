"""Rebuild every run's metrics from its saved stdout and artefacts with the current parsers.

    python -m scripts.bench.alt.reparse docs/performance/alt-bench/results/<run-id>

For when a parser learned to read more (another engine's timings, another section) after the runs were taken. Only the ``metrics``
object of each result file is replaced, and only when it differs; the status, timings and argv are left alone.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from scripts.bench.alt import suites as su


def reparse(results_dir):
    changed = 0
    for path in sorted(Path(results_dir).glob("*_?_*.json")):
        doc = json.loads(path.read_text())
        suite = su.SUITES_BY_ID.get(doc.get("suite"))
        if suite is None or "side" not in doc:
            continue
        base = path.with_suffix("")
        text_path = Path(str(base) + ".txt")
        outdir = Path(str(base) + ".out")
        stdout = text_path.read_text(errors="replace") if text_path.exists() else ""
        parser = su.PARSERS.get(suite.parser)
        if parser is None or not outdir.exists():
            continue
        try:
            metrics = parser(stdout, su.context(outdir, outdir, 0))
        except Exception:
            continue
        # keep the harness's own wall_total / elapsed figure
        for keep in ("wall_total",):
            if keep in (doc.get("metrics") or {}) and keep not in metrics:
                metrics[keep] = doc["metrics"][keep]
        merged = {**(doc.get("metrics") or {}), **metrics}
        if merged != doc.get("metrics"):
            doc["metrics"] = merged
            path.write_text(json.dumps(doc))
            changed += 1
    return changed


def main(argv):
    for d in argv:
        print(f"{d}: {reparse(d)} result files updated")


if __name__ == "__main__":
    main(sys.argv[1:])
