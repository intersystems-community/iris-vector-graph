"""The SNB queries no benchmark leg has run yet. A leg (snb_worker.py query) ends at the first query that outlives its cap,
so the queries after it need another leg; run_native.sh asks this module which, until none are left.

Usage: python -m scripts.bench.alt.legs <leg.json> [<leg.json> ...]   (prints the names, one line, space-separated)
"""

from __future__ import annotations

import json
import sys
from typing import Iterable, List


def remaining(names: Iterable[str], paths: Iterable[str]) -> List[str]:
    """The names, in order, that no leg's results hold (a timed-out query is held: it is not run again)."""
    done = set()
    for p in paths:
        try:
            with open(p) as f:
                done.update(json.load(f).get("results", {}))
        except (OSError, ValueError):
            continue
    return [n for n in names if n not in done]


def main(argv=None) -> None:
    from scripts.bench.alt.snb_queries import QUERIES

    print(" ".join(remaining(list(QUERIES), (argv if argv is not None else sys.argv[1:]))))


if __name__ == "__main__":
    main()
