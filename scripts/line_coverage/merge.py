#!/usr/bin/env python3
"""Print the merged raw and adjusted figures of line-monitor dumps: ``merge.py DUMP [DUMP ...]``."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.line_coverage import linecov  # noqa: E402

dumps = [linecov.load(Path(p)) for p in sys.argv[1:]]
floor = linecov.load_floor()
for adjusted in (False, True):
    covered, total, pct = linecov.overall(linecov.merge(dumps, adjusted, floor.get("excluded", {})))
    print(f"{'adjusted' if adjusted else 'raw':9s} {covered}/{total}  {pct:.1f}%")
