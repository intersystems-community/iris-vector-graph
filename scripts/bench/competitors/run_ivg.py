#!/usr/bin/env python3
"""The traversal suites of run_queries.py on IVG, through the same plans, seeds, expected answers and timing
(run_queries.run_spec), so IVG's records sit beside Neo4j's and Memgraph's in one .jsonl.

    python scripts/bench/competitors/run_ivg.py --dataset DRKG --namespace DRKGB --out results/DRKG.jsonl
    python scripts/bench/competitors/run_ivg.py --dataset DRKG --namespace DRKGB --native 0 ...   # the SQL path

IVG runs in this process over the DB-API (the competitors are reached over Bolt by their drivers); each timed call is
`execute_cypher` with every row fetched. The engine is `ivg-native` with native Cypher on (the default), `ivg-sql`
with it off. An IVG DRKG load needs `ivg_drkg_align.py` first: the suite's `MATCH (s:Entity {id: $x})` reads the
competitors' node model.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import datasets  # noqa: E402
import run_queries  # noqa: E402


class IVG:
    """run_spec's engine interface over an IRISGraphEngine."""

    def __init__(self, engine, name: str):
        self.engine, self.name = engine, name

    def run(self, cypher: str, params: Optional[Dict[str, Any]] = None, timeout_s: Optional[float] = None) -> List[Any]:
        result = self.engine.execute_cypher(cypher, params or {})
        if getattr(result, "error", None):
            # execute_cypher reports a failure on the result: timed as rows, it would read as a fast wrong answer
            raise RuntimeError(result.error)
        return [list(row) for row in result.rows]

    def reset_session(self) -> None:
        pass


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--namespace", required=True)
    ap.add_argument("--out", type=Path, required=True, help="append records to this .jsonl")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=31972)
    ap.add_argument("--native", type=int, default=1, help="1: native Cypher on (ivg-native), 0: off (ivg-sql)")
    ap.add_argument("--warmup", type=int, default=3)
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--only", help="comma-separated query ids")
    ap.add_argument("--drkg-max-depth", type=int, default=2)
    args = ap.parse_args(argv)

    import iris

    from iris_vector_graph.engine import IRISGraphEngine

    graph = datasets.get_graph(args.dataset)
    conn = iris.connect(args.host, args.port, args.namespace, "_SYSTEM", "SYS")
    eng = IVG(IRISGraphEngine(conn, native_cypher=bool(args.native)), "ivg-native" if args.native else "ivg-sql")
    plan = run_queries.traversal_plan(graph, "ivg", args.drkg_max_depth)
    if args.only:
        plan = [p for p in plan if p.id in set(args.only.split(","))]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    print(f"{eng.name}  namespace={args.namespace}  dataset={graph.name}  warmup={args.warmup} runs={args.runs}")
    for spec in plan:
        run_queries.run_spec(eng, spec, args.warmup, args.runs, 600.0, args.out, {"dataset": graph.name, "namespace": args.namespace})
    return 0


if __name__ == "__main__":
    sys.exit(main())
