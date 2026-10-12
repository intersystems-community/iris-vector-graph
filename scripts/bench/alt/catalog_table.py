#!/usr/bin/env python3
"""Render the registered-suite table of docs/performance/alt-bench/CATALOG.md from the registry (spec 250).

    python scripts/bench/alt/catalog_table.py --smoke docs/performance/alt-bench/results/smoke-3 --write

Replaces the text between the `suite-table` markers (or the `SUITE-TABLE` placeholder) in the catalog. The runtime column
is the elapsed time of rep 1 on side A in the smoke directory, when given.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts.bench.alt import suites as su  # noqa: E402

CATALOG = Path(__file__).resolve().parents[3] / "docs" / "performance" / "alt-bench" / "CATALOG.md"
START, END = "<!-- suite-table:start -->", "<!-- suite-table:end -->"


def smoke_times(smoke_dirs):
    """{suite: (elapsed_s, status)} of rep 1 on side A; later directories override earlier ones."""
    if isinstance(smoke_dirs, (str, Path)):
        smoke_dirs = [smoke_dirs]
    out = {}
    for smoke_dir in smoke_dirs or []:
        for p in sorted(Path(smoke_dir).glob("*_A_1.json")):
            d = json.loads(p.read_text())
            if "suite" in d and "elapsed_s" in d:
                both = (Path(smoke_dir) / p.name.replace("_A_1", "_B_1")).exists()
                out[d["suite"]] = (d["elapsed_s"], d.get("status", "ok"), both)
    return out


def render(smoke_dir=None):
    times = smoke_times(smoke_dir)
    rows = [
        "| id | command (from the side tree) | data | quick | wipes | Arno | smoke (A, 1 rep) | state |",
        "| --- | --- | --- | --- | --- | --- | ---: | --- |",
    ]
    for s in su.SUITES:
        cmd = " ".join(a for a in s.argv if not a.startswith("--junitxml") and a not in ("-p", "no:cacheprovider", "--durations=0"))
        cmd = cmd.replace("{py}", "python").replace("{out}", "OUT").replace("{port}", "31972").replace("{ns}", "NS")
        cmd = re.sub(r"\s+", " ", cmd)
        t = times.get(s.id)
        if s.blocked:
            state = "blocked: duplicate rows in executemany (see 1.4)"
        elif t:
            state = ("ran on A and B" if t[2] else "launched on A only") + ("" if t[1] == "ok" else f", status {t[1]}")
        else:
            state = "registered, not launched (size)"
        rows.append(
            f"| `{s.id}` | `{cmd}` | {s.data or 'self'} | {'yes' if s.quick else 'no'} | {'yes' if s.wipes else 'no'} | "
            f"{'yes' if s.needs_arno else 'no'} | {f'{t[0]} s' if t else 'n/a'} | {state} |"
        )
    return "\n".join(rows)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--smoke", nargs="+")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    table = render(args.smoke)
    if not args.write:
        print(table)
        return 0
    text = CATALOG.read_text()
    block = f"{START}\n{table}\n{END}"
    if START in text:
        text = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda m: block, text, flags=re.S)
    else:
        text = text.replace("<!-- SUITE-TABLE -->", block)
    CATALOG.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
