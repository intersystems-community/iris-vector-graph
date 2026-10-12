#!/usr/bin/env python3
"""Traversal suites on Neo4j and Memgraph, mirroring the repo's benchmarks. One .jsonl record per measured query.

    python run_queries.py --engine neo4j --dataset S --out results/S.jsonl
    python run_queries.py --engine both  --dataset M --warmup 3 --runs 20 --out results/M.jsonl

Suites (all run by default for the dataset's family):
    bench_q          tests/benchmarks/bench.py Q1..Q6 (RMAT datasets)
    bench193         tests/benchmarks/bench_193_full.py section 5, the literal `[*1..N]` query (RMAT datasets)
    neo4j_comparison benchmarks/neo4j_comparison.py Q1, Q2a, Q3 (NCM)
    ldbc             benchmarks/three_engine_benchmark.py IC2, IC3, IC13, COUNT (LDBC*)
    drkg             1/2-hop counts and a shortest path on DRKG

Seeds and start nodes come from datasets.py (see its docstrings and the README for the rule).
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import datasets
import engines
from engines import Engine, q
from jsonl import make_record, time_runs, write_record

RMAT_FAMILY = {"S", "M", "L", "S193", "M193", "L193"}


@dataclass
class QuerySpec:
    id: str
    suite: str
    cypher: str
    params: Dict[str, Any]
    # how to read the count out of the rows: "scalar" = first column of the first row, "rows" = number of rows
    kind: str = "scalar"
    expect: Optional[int] = None
    note: str = ""
    setup: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)
    teardown: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Plans
# ---------------------------------------------------------------------------


def _bench_q(graph: datasets.Graph, engine: str) -> List[QuerySpec]:
    lab, prop, rel = q(graph.label), q(graph.node_prop), q(graph.rel_type)
    seed = datasets.highest_out_degree_seed(graph)
    reach = datasets.reach_counts(graph, seed, 4)
    reach_bfs = datasets.reach_counts(graph, seed, 4, include_seed=False)
    src, dst = datasets.ivg_sp_pair(graph, seed)
    _, dst_d = datasets.ivg_sp_pair_distinct(graph, seed)
    suite = "bench_q"
    plan: List[QuerySpec] = [
        QuerySpec(
            "Q1", suite,
            f"MATCH (s:{lab})-[:{rel}]->(n) WHERE s.{prop} = $id RETURN count(n) AS cnt",
            {"id": seed}, expect=reach[0],
            note="bench.py Q1; the IVG query is unlabeled (s.node_id = $id), here labeled so the index is used",
        )
    ]
    for d in (2, 3, 4):
        plan.append(
            QuerySpec(
                f"Q{d}", suite,
                f"MATCH (n:{lab} {{{prop}: $x}})-[:{rel}*1..{d}]->(m) RETURN count(DISTINCT m) AS cnt",
                {"x": seed}, expect=reach[d - 1],
                note=f"bench.py Q{d} (IVG: BFSFastJson depth {d}, an edge list); distinct nodes reached by trails of 1..{d} hops",
            )
        )
        if engine == "memgraph":
            plan.append(
                QuerySpec(
                    f"Q{d}_bfs", suite,
                    f"MATCH (n:{lab} {{{prop}: $x}})-[:{rel} *BFS ..{d}]->(m) RETURN count(DISTINCT m) AS cnt",
                    {"x": seed}, expect=reach_bfs[d - 1],
                    note=f"Memgraph's BFS expansion operator (set semantics, like BFSFastJson); depth {d}. "
                    "It never returns its start node, so a seed that lies on a cycle is one short of the trail count",
                )
            )

    def sp(qid: str, a: str, b: Optional[str], note: str) -> Optional[QuerySpec]:
        if b is None:
            return None
        dist = datasets.undirected_distance(graph, a, b, 8)
        if engine == "memgraph":
            # Memgraph has no shortestPath(); its BFS expansion finds the shortest path, and returns no row for a == b
            return QuerySpec(
                qid, suite,
                f"MATCH p = (a:{lab} {{{prop}: $a}})-[*BFS ..8]-(b:{lab} {{{prop}: $b}}) RETURN size(relationships(p)) AS hops",
                {"a": a, "b": b}, expect=None if a == b else dist,
                note=note + "; Memgraph form: *BFS ..8 (undirected), no row when the endpoints are the same node",
            )
        return QuerySpec(
            qid, suite,
            f"MATCH p = shortestPath((a:{lab} {{{prop}: $a}})-[*..8]-(b:{lab} {{{prop}: $b}})) RETURN length(p) AS hops",
            {"a": a, "b": b}, expect=dist, note=note,
        )

    def weighted(qid: str, a: str, b: Optional[str], note: str) -> Optional[QuerySpec]:
        if b is None:
            return None
        dist = datasets.directed_distance(graph, a, b, 10)
        if engine == "memgraph":
            return QuerySpec(
                qid, suite,
                f"MATCH p = (a:{lab} {{{prop}: $a}})-[:{rel} *WSHORTEST 10 (r, n | 1.0) total_weight]->(b:{lab} {{{prop}: $b}}) "
                "RETURN size(relationships(p)) AS hops, total_weight",
                {"a": a, "b": b}, expect=None if a == b else dist,
                note=note + "; Memgraph form: *WSHORTEST with a unit lambda. Its total_weight is hops + 1 (the lambda also "
                "prices the start vertex), so the first column is the hop count; no row when the endpoints are the same node",
            )
        g = f"ivg_q6_{graph.name}"
        return QuerySpec(
            qid, suite,
            f"MATCH (a:{lab} {{{prop}: $a}}), (b:{lab} {{{prop}: $b}}) "
            f"CALL gds.shortestPath.dijkstra.stream('{g}', {{sourceNode: a, targetNode: b}}) YIELD totalCost "
            "RETURN totalCost",
            {"a": a, "b": b}, expect=dist, note=note,
            setup=[
                (f"CALL gds.graph.drop('{g}', false) YIELD graphName RETURN graphName", {}),
                (f"CALL gds.graph.project('{g}', '{graph.label}', '{graph.rel_type}') YIELD graphName RETURN graphName", {}),
            ],
            teardown=[(f"CALL gds.graph.drop('{g}', false) YIELD graphName RETURN graphName", {})],
            extra={"implementation": "gds.shortestPath.dijkstra (unit weights; projection built once, not timed)"},
        )

    q5_note = "bench.py Q5 with the IVG pair rule (pick_shortest_path_pair): on RMAT S and M the pair is (seed, seed)"
    q6_note = (
        "bench.py Q6 with unit weights. The IVG call, `ivg.shortestPath.weighted(.., 'weight', ..)`, returned no row "
        "in the recorded runs because these edges carry no weight property"
    )
    for spec in (
        sp("Q5", src, dst, q5_note),
        sp("Q5_distinct", src, dst_d, "Q5 with the first maximum-step target that is not the seed"),
        weighted("Q6", src, dst, q6_note),
        weighted("Q6_distinct", src, dst_d, "Q6 with the distinct pair"),
    ):
        if spec:
            plan.append(spec)

    # bench_193_full.py section 5, literally: unlabeled start node, untyped relationship
    for h in (2, 3, 4):
        plan.append(
            QuerySpec(
                f"B193_h{h}", "bench193",
                f"MATCH (n {{{prop}: $x}})-[*1..{h}]->(m) RETURN count(distinct m) AS cnt",
                {"x": seed}, expect=reach[h - 1],
                note="bench_193_full.py bench_neo4j, verbatim: no label on n, so a server without a label scan visits every node",
                extra={"labeled": False},
            )
        )
    return plan


def _neo4j_comparison(graph: datasets.Graph, engine: str) -> List[QuerySpec]:
    lab, prop = q(graph.label), q(graph.node_prop)
    seed = datasets.highest_total_degree_seed(graph)
    reach = datasets.reach_counts(graph, seed, 3)
    suite = "neo4j_comparison"
    # reach[1] counts targets of 1..2 hops; Q2a counts exactly-2-hop trails' targets
    return [
        QuerySpec("NC_Q1", suite, f"MATCH (s:{lab} {{{prop}: $id}})-[:R]->(n) RETURN count(n) AS cnt", {"id": seed},
                  expect=datasets.out_degree(graph, seed), note="neo4j_comparison.py Q1"),
        QuerySpec("NC_Q2a", suite, f"MATCH (s:{lab} {{{prop}: $id}})-[:R*2..2]->(n) RETURN count(DISTINCT n) AS cnt",
                  {"id": seed}, note="neo4j_comparison.py Q2a (exactly two hops)"),
        QuerySpec("NC_Q3", suite, f"MATCH (s:{lab} {{{prop}: $id}})-[:R*1..3]->(n) RETURN count(DISTINCT n) AS cnt",
                  {"id": seed}, expect=reach[2], note="neo4j_comparison.py Q3"),
    ]


def ldbc_plan(graph: datasets.Graph, engine: str) -> List[QuerySpec]:
    lab, prop, rel = q(graph.label), q(graph.node_prop), q(graph.rel_type)
    seed, other = datasets.LDBC_SEEDS[0], datasets.LDBC_SEEDS[1]
    suite = "ldbc"
    present = set(graph.nodes) if len(graph.nodes) < 2_000_000 else set()
    out_deg = datasets.out_degree(graph, seed) if seed in present else None
    notes = "" if seed in present else f"seed {seed} is not in this dataset"
    return [
        QuerySpec("IC2", suite, f"MATCH (a:{lab} {{{prop}: $seed}})-[:{rel}]->(b:{lab}) RETURN b.{prop}", {"seed": seed},
                  kind="rows", expect=out_deg, note=notes or "three_engine_benchmark.py IC2: 1-hop KNOWS"),
        QuerySpec("IC3", suite,
                  f"MATCH (a:{lab} {{{prop}: $seed}})-[:{rel}*1..2]->(b:{lab}) RETURN DISTINCT b.{prop}", {"seed": seed},
                  kind="rows", note=notes or "three_engine_benchmark.py IC3: 1..2-hop KNOWS, distinct"),
        QuerySpec("IC13", suite,
                  f"MATCH p = shortestPath((a:{lab} {{{prop}: $src}})-[:{rel}*..6]-(b:{lab} {{{prop}: $dst}})) RETURN length(p)",
                  {"src": seed, "dst": other}, kind="rows", note=notes or "three_engine_benchmark.py IC13: shortestPath, <= 6 hops"),
        QuerySpec("COUNT_friends", suite,
                  f"MATCH (a:{lab} {{{prop}: $seed}})-[:{rel}]->(b:{lab}) RETURN count(b) AS c", {"seed": seed},
                  expect=out_deg, note=notes or "three_engine_benchmark.py COUNT friends"),
    ]


def _drkg(graph: datasets.Graph, engine: str, max_depth: int) -> List[QuerySpec]:
    lab, prop = q(graph.label), q(graph.node_prop)
    seed = datasets.highest_out_degree_seed(graph)
    reach = datasets.reach_counts(graph, seed, max_depth)
    reach_bfs = datasets.reach_counts(graph, seed, max_depth, include_seed=False)
    _, dst = datasets.ivg_sp_pair_distinct(graph, seed)
    suite = "drkg"
    plan = [
        QuerySpec("D_Q1", suite, f"MATCH (s:{lab} {{{prop}: $x}})-[r]->(n) RETURN count(r) AS cnt", {"x": seed},
                  expect=datasets.out_degree(graph, seed), note="out-edges of the highest out-degree entity"),
    ]
    for d in range(2, max_depth + 1):
        plan.append(
            QuerySpec(f"D_Q{d}", suite, f"MATCH (n:{lab} {{{prop}: $x}})-[*1..{d}]->(m) RETURN count(DISTINCT m) AS cnt",
                      {"x": seed}, expect=reach[d - 1], note=f"distinct entities within {d} hops, all relation types")
        )
        if engine == "memgraph":
            plan.append(
                QuerySpec(f"D_Q{d}_bfs", suite,
                          f"MATCH (n:{lab} {{{prop}: $x}})-[*BFS ..{d}]->(m) RETURN count(DISTINCT m) AS cnt",
                          {"x": seed}, expect=reach_bfs[d - 1], note=f"Memgraph BFS operator, depth {d} (never returns the start node)")
            )
    if dst:
        plan.append(
            QuerySpec("D_SP", suite,
                      f"MATCH p = shortestPath((a:{lab} {{{prop}: $a}})-[*..8]-(b:{lab} {{{prop}: $b}})) RETURN length(p) AS hops",
                      {"a": seed, "b": dst}, expect=datasets.undirected_distance(graph, seed, dst, 8),
                      note="shortest path from the seed to the first maximum-step target within 3 hops")
        )
    return plan


def traversal_plan(graph: datasets.Graph, engine: str, drkg_max_depth: int = 2) -> List[QuerySpec]:
    if graph.name in RMAT_FAMILY:
        return _bench_q(graph, engine)
    if graph.name == "NCM":
        return _neo4j_comparison(graph, engine)
    if graph.name.startswith("LDBC"):
        return ldbc_plan(graph, engine)
    if graph.name == "DRKG":
        return _drkg(graph, engine, drkg_max_depth)
    return []


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def _count(spec: QuerySpec, rows: List[Any]) -> Optional[Any]:
    if spec.kind == "rows":
        return len(rows)
    return rows[0][0] if rows else None


def run_spec(eng: Engine, spec: QuerySpec, warmup: int, runs: int, timeout_s: float, out: Path, base: Dict[str, Any], log=print) -> Dict[str, Any]:
    params = {**base, "cypher": spec.cypher, "bindings": spec.params, "note": spec.note, **spec.extra}
    for text, binds in spec.setup:
        t0 = time.perf_counter()
        try:
            eng.run(text, binds)
        except Exception as exc:
            rec = make_record(eng.name, spec.suite, spec.id, [], {**params, "error": f"setup failed: {str(exc)[:300]}"})
            write_record(out, rec)
            log(f"  {eng.name:8s} {spec.id:14s} SETUP FAILED {str(exc)[:100]}")
            return rec
    if spec.setup:
        write_record(
            out,
            make_record(eng.name, spec.suite, f"{spec.id}_setup", [(time.perf_counter() - t0) * 1000.0],
                        {**base, "note": "setup of the in-memory projection; not part of the query's latency", "cypher": spec.setup[-1][0]}),
        )
    box: Dict[str, Any] = {}

    def once():
        rows = eng.run(spec.cypher, spec.params, timeout_s)
        box["rows"] = rows
        return rows

    values, last = time_runs(once, warmup=warmup, runs=runs, catch=True)
    if isinstance(last, Exception):
        eng.reset_session()
        rec = make_record(eng.name, spec.suite, spec.id, [], {**params, "error": f"{type(last).__name__}: {str(last)[:300]}"})
        log(f"  {eng.name:8s} {spec.id:14s} ERROR {type(last).__name__}: {str(last)[:100]}")
    else:
        got = _count(spec, last)
        params["result"] = got
        if spec.expect is not None:
            params["expected"] = spec.expect
            params["match"] = bool(got == spec.expect)
        rec = make_record(eng.name, spec.suite, spec.id, values, params)
        med = sorted(values)[len(values) // 2] if values else float("nan")
        flag = "" if spec.expect is None else ("  ok" if params["match"] else f"  MISMATCH expected {spec.expect}")
        log(f"  {eng.name:8s} {spec.id:14s} n={len(values):2d} p50={med:9.3f} ms  result={got}{flag}")
    write_record(out, rec)
    for text, binds in spec.teardown:
        try:
            eng.run(text, binds)
        except Exception:
            pass
    return rec


def write_meta(path: Path, eng: Engine, graph: datasets.Graph, args: argparse.Namespace, plan: List[QuerySpec]) -> None:
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    meta: Dict[str, Any] = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
    meta.setdefault("engines", {})[eng.name] = {"version": eng.version(), "gds": eng.gds_version(), "uri": eng.uri}
    meta["dataset"] = datasets.summary(graph)
    meta["seed_node"] = datasets.highest_out_degree_seed(graph) if graph.name != "NCM" else datasets.highest_total_degree_seed(graph)
    meta["warmup"], meta["runs"], meta["timeout_s"] = args.warmup, args.runs, args.timeout_s
    meta["host"] = platform.platform()
    meta["written"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    meta["queries"] = [s.id for s in plan]
    meta_path.write_text(json.dumps(meta, indent=2, default=str))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", choices=["neo4j", "memgraph", "both"], required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", type=Path, required=True, help="append records to this .jsonl")
    ap.add_argument("--warmup", type=int, default=3)
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--timeout-s", type=float, default=120.0, help="server-side transaction timeout (Neo4j); Memgraph uses its --query-execution-timeout-sec")
    ap.add_argument("--only", help="comma-separated query ids")
    ap.add_argument("--suite", help="comma-separated suite names")
    ap.add_argument("--drkg-max-depth", type=int, default=2)
    ap.add_argument("--neo4j-uri")
    ap.add_argument("--memgraph-uri")
    args = ap.parse_args(argv)

    graph = datasets.get_graph(args.dataset)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    rc = 0
    for name in (["neo4j", "memgraph"] if args.engine == "both" else [args.engine]):
        eng = engines.connect(name, uri=args.neo4j_uri if name == "neo4j" else args.memgraph_uri)
        try:
            plan = traversal_plan(graph, name, args.drkg_max_depth)
            if args.only:
                keep = set(args.only.split(","))
                plan = [p for p in plan if p.id in keep]
            if args.suite:
                keep = set(args.suite.split(","))
                plan = [p for p in plan if p.suite in keep]
            if not plan:
                print(f"no traversal queries defined for dataset {graph.name}", file=sys.stderr)
                rc = 2
                continue
            # the database must hold this dataset; fail loudly if it does not
            n_db = eng.node_count(graph.label)
            if n_db != len(graph.nodes):
                print(f"{name}: database holds {n_db} :{graph.label} nodes, dataset {graph.name} has {len(graph.nodes)}. "
                      f"Start the engine on this dataset's volume and load it first.", file=sys.stderr)
                rc = 2
                continue
            write_meta(args.out, eng, graph, args, plan)
            print(f"{name} {eng.version()}  dataset={graph.name}  warmup={args.warmup} runs={args.runs}")
            base = {"dataset": graph.name}
            for spec in plan:
                run_spec(eng, spec, args.warmup, args.runs, args.timeout_s, args.out, base)
        finally:
            eng.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
