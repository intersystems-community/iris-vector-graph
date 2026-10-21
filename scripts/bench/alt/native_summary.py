"""IVG's SNB timings against Neo4j's and Memgraph's, on the parameter sets both sides ran.

The IVG harness (snb_worker) shortens a slow query's leg to its first few parameter sets (its `plan`: sets, warmup,
reps); the competitors (competitors/snb.py) time all of them. Both keep their timings per round over the sets in order,
so a competitor's are cut to the sets IVG ran before the medians are compared, and the answers (signatures) are
compared on those sets only.

Usage: python -m scripts.bench.alt.native_summary <competitor results dir> <ivg query json> [<sql query json>]
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Optional


def on_sets(values: list, sets: int, keep: int) -> list:
    """The timings of the first `keep` of `sets` parameter sets, from timings kept round by round over them."""
    if keep >= sets:
        return list(values)
    return [v for i, v in enumerate(values) if i % sets < keep]


def _p50(values):
    return statistics.median(values) if values else None


def row(query: str, ivg: dict, neo: Optional[dict], mg: Optional[dict], sql: Optional[dict] = None) -> dict:
    sigs = ivg.get("signatures") or []
    keep = (ivg.get("plan") or [len(sigs) or 1])[0]

    def cut(rec):
        if not rec:
            return None
        total = len((rec.get("params") or {}).get("signatures") or []) or 1
        return _p50(on_sets(rec.get("values_ms") or [], total, keep))

    nsig = ((neo or {}).get("params") or {}).get("signatures") or []
    n = min(len(sigs), len(nsig))
    out = {
        "query": query,
        "ivg": _p50(ivg.get("values_ms") or []),
        "neo4j": cut(neo),
        "memgraph": cut(mg),
        "sql": _p50((sql or {}).get("values_ms") or []) if sql else None,
        "sets": keep,
        "agree": sum(a == b for a, b in zip(sigs[:n], nsig[:n])),
        "compared": n,
        "error": ivg.get("error"),
    }
    out["ratio"] = out["ivg"] / out["neo4j"] if out["ivg"] and out["neo4j"] else None
    return out


def summarize(comp_dir: str, ivg_json: str, sql_json: Optional[str] = None) -> list:
    ivg = json.loads(Path(ivg_json).read_text())["results"]
    sql = json.loads(Path(sql_json).read_text())["results"] if sql_json else {}
    comp = {}
    for eng in ("neo4j", "memgraph"):
        f = Path(comp_dir) / f"{eng}_snb.jsonl"
        comp[eng] = {r["query"]: r for r in map(json.loads, f.read_text().splitlines())} if f.exists() else {}
    return [row(q, ivg[q], comp["neo4j"].get(q), comp["memgraph"].get(q), sql.get(q)) for q in ivg]


def render(rows: list) -> str:
    f = lambda x: f"{x:10.1f}" if x is not None else "         -"  # noqa: E731
    lines = [f"{'query':34} {'SQL ms':>10} {'IVG ms':>10} {'Neo4j':>10} {'Memgr':>10} {'IVG/Neo':>8} sets =Neo4j"]
    for r in sorted(rows, key=lambda r: -(r["ratio"] or 0)):
        ratio = f"{r['ratio']:8.2f}" if r["ratio"] is not None else "       -"
        err = f"  ERROR {str(r['error'])[:50]}" if r["error"] else ""
        lines.append(
            f"{r['query']:34} {f(r['sql'])} {f(r['ivg'])} {f(r['neo4j'])} {f(r['memgraph'])} {ratio} {r['sets']:4d}"
            f" {r['agree']}/{r['compared']}{err}"
        )
    ratios = [r["ratio"] for r in rows if r["ratio"] is not None]
    if ratios:
        lines.append(
            f"\n{len(rows)} queries, {len(ratios)} timed on both: median IVG/Neo4j {statistics.median(ratios):.2f}, "
            f"at or ahead (<=1.1x) {sum(x <= 1.1 for x in ratios)}, within 3x {sum(x <= 3 for x in ratios)}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    print(render(summarize(*sys.argv[1:4])))
