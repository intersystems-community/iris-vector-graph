#!/usr/bin/env python3
"""The ObjectScript line-coverage gate.

    python scripts/line_coverage/check_gate.py DUMP [DUMP ...]
    python scripts/line_coverage/check_gate.py --report DUMP [...]     # per-class table, no pass/fail

DUMPs are the files ``scripts/line_coverage/run_gate.sh`` produces (one per namespace and chunk).
Exits 0 when the union of the dumps meets docs/objectscript-coverage-floor.json, 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.line_coverage import linecov  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dumps", nargs="+", type=Path)
    ap.add_argument("--floor", type=Path, default=linecov.FLOOR_PATH)
    ap.add_argument("--report", action="store_true", help="print the per-class table and exit 0")
    args = ap.parse_args(argv)

    dumps = [linecov.load(p) for p in args.dumps]
    floor = linecov.load_floor(args.floor)
    if args.report:
        per_class = linecov.merge(dumps, skip=floor.get("excluded", {}))
        for routine, (c, t) in sorted(per_class.items(), key=lambda kv: kv[1][0] - kv[1][1]):
            print(f"{routine:45s} {c:5d}/{t:5d}  {100.0 * c / t if t else 100.0:5.1f}%  uncovered={t - c}")
        covered, total, pct = linecov.overall(per_class)
        print(f"{'ALL':45s} {covered:5d}/{total:5d}  {pct:5.1f}%")
        return 0
    ok, messages, pct = linecov.check(dumps, floor)
    print(f"ObjectScript line coverage (adjusted): {pct:.1f}%  floor {floor['adjusted_percent_min']}%")
    for m in messages:
        print("  FAIL:" if not ok else "  note:", m)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
