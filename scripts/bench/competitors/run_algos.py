#!/usr/bin/env python3
"""Algorithm suites: PageRank, degree centrality, WCC/SCC, betweenness, closeness, Louvain/Leiden, triangles.

    python run_algos.py --engine neo4j     --dataset S --out results/S_algos.jsonl     # Neo4j GDS
    python run_algos.py --engine memgraph  --dataset S --out results/S_algos.jsonl     # Memgraph procedures
    python run_algos.py --engine networkx  --dataset S --out results/S_algos.jsonl     # reference
    python run_algos.py --engine igraph    --dataset S --out results/S_algos.jsonl     # reference
    python run_algos.py --engine all       --dataset karate

These mirror tests/perf/test_head_to_head.py (degree, betweenness, closeness, community detection on karate, er500,
er2000), test_betweenness_vs_gds.py (exact and 200-source sampled betweenness on karate, btw500, btw2000) and
test_leiden_four_way.py (networkx Louvain, igraph leidenalg, GDS Leiden, all seed 42). The same legs run on the
RMAT and DRKG graphs. Where an engine has no procedure for an algorithm the leg is recorded as skipped, and the
networkx or igraph record for the same algorithm is the reference.

Timed region: the procedure call and fetching every result row (as the existing tests do, which also look up the node
id of each row). Building the GDS projection and the networkx/igraph graph are separate records (`*_project`,
`build_graph`) and are not part of an algorithm's latency.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import datasets
import engines
import refalgos
from engines import Engine, q
from jsonl import make_record, time_runs, write_record

ALGO_ENGINES = ["neo4j", "memgraph", "networkx", "igraph"]
GDS_EXACT_MAX_NODES = 20_000


# ---------------------------------------------------------------------------
# Legs for the database engines. A leg is (cypher, summary) where summary(rows) -> dict
# ---------------------------------------------------------------------------


def _scores(rows) -> Dict[str, Any]:
    return {"scores": {r[0]: float(r[1]) for r in rows}}


def _distinct(col: int = 0):
    def f(rows):
        return {"communities": len({r[col] for r in rows})}

    return f


def _components(rows) -> Dict[str, Any]:
    return {"components": len({r[0] for r in rows})}


def gds_legs(graph: datasets.Graph, nat: str, und: str) -> Dict[str, Tuple[str, Callable, Dict[str, Any]]]:
    pid = f"gds.util.asNode(nodeId).{q(graph.node_prop)} AS id"
    n = len(graph.nodes)
    legs: Dict[str, Tuple[str, Callable, Dict[str, Any]]] = {
        "pagerank": (
            f"CALL gds.pageRank.stream('{nat}', {{dampingFactor: 0.85, maxIterations: 20, tolerance: 0.0}}) "
            f"YIELD nodeId, score RETURN {pid}, score",
            _scores, {"implementation": "gds.pageRank.stream", "damping": 0.85, "max_iterations": 20},
        ),
        "degree_out": (
            f"CALL gds.degree.stream('{nat}') YIELD nodeId, score RETURN {pid}, score",
            _scores, {"implementation": "gds.degree.stream (NATURAL)"},
        ),
        "degree_both": (
            f"CALL gds.degree.stream('{und}') YIELD nodeId, score RETURN {pid}, score",
            _scores, {"implementation": "gds.degree.stream (UNDIRECTED projection)"},
        ),
        "wcc": (
            f"CALL gds.wcc.stream('{nat}') YIELD nodeId, componentId RETURN componentId",
            _components, {"implementation": "gds.wcc.stream"},
        ),
        "scc": (
            f"CALL gds.scc.stream('{nat}') YIELD nodeId, componentId RETURN componentId",
            _components, {"implementation": "gds.scc.stream"},
        ),
        "betweenness_sampled200": (
            f"CALL gds.betweenness.stream('{und}', {{samplingSize: {refalgos.BETWEENNESS_SAMPLE}, samplingSeed: 42}}) "
            f"YIELD nodeId, score RETURN {pid}, score",
            _scores, {"implementation": "gds.betweenness.stream", "sampled": True, "sample_size": 200, "seed": 42},
        ),
        "louvain": (
            f"CALL gds.louvain.stream('{und}') YIELD nodeId, communityId RETURN communityId",
            _distinct(), {"implementation": "gds.louvain.stream"},
        ),
        "leiden": (
            f"CALL gds.leiden.stream('{und}', {{randomSeed: 42}}) YIELD nodeId, communityId RETURN communityId",
            _distinct(), {"implementation": "gds.leiden.stream", "seed": 42},
        ),
        "triangle_count": (
            f"CALL gds.triangleCount.stream('{und}') YIELD nodeId, triangleCount RETURN triangleCount",
            lambda rows: {"triangles": sum(int(r[0]) for r in rows) // 3}, {"implementation": "gds.triangleCount.stream"},
        ),
    }
    if n <= GDS_EXACT_MAX_NODES:
        legs["betweenness_exact"] = (
            f"CALL gds.betweenness.stream('{und}') YIELD nodeId, score RETURN {pid}, score",
            _scores, {"implementation": "gds.betweenness.stream", "sampled": False},
        )
        legs["closeness_harmonic"] = (
            f"CALL gds.closeness.harmonic.stream('{und}') YIELD nodeId, score RETURN {pid}, score",
            _scores, {"implementation": "gds.closeness.harmonic.stream"},
        )
    return legs


def memgraph_legs(graph: datasets.Graph, procs: List[str]) -> Dict[str, Tuple[Optional[str], Optional[Callable], Dict[str, Any]]]:
    """Memgraph's community-edition image carries no native graph-algorithm library (MAGE ships in a separate image).
    What it has: Cypher aggregation, wcc.get_components, and the nxalg module, which runs networkx inside the server."""
    lab, prop = q(graph.label), q(graph.node_prop)
    have = set(procs)
    legs: Dict[str, Tuple[Optional[str], Optional[Callable], Dict[str, Any]]] = {
        "degree_out": (
            f"MATCH (n:{lab}) OPTIONAL MATCH (n)-[r]->() RETURN n.{prop} AS id, count(r) AS score",
            _scores, {"implementation": "cypher aggregation"},
        ),
        "degree_both": (
            f"MATCH (n:{lab}) OPTIONAL MATCH (n)-[r]-() RETURN n.{prop} AS id, count(r) AS score",
            _scores, {"implementation": "cypher aggregation (both directions; a self loop counts once)"},
        ),
    }

    def add(algo: str, needs: str, cypher: str, summ: Callable, params: Dict[str, Any]) -> None:
        if needs in have:
            legs[algo] = (cypher, summ, params)
        else:
            legs[algo] = (None, None, {"skipped": f"memgraph image has no {needs}"})

    add("pagerank", "nxalg.pagerank",
        f"CALL nxalg.pagerank() YIELD node, rank RETURN node.{prop} AS id, rank",
        _scores, {"implementation": "nxalg.pagerank (networkx inside Memgraph)", "alpha": 0.85, "max_iter": 100, "tol": 1e-6})
    add("wcc", "wcc.get_components",
        f"MATCH (n:{lab}) WITH collect(n) AS ns MATCH (:{lab})-[r]->(:{lab}) WITH ns, collect(r) AS rs "
        "CALL wcc.get_components(ns, rs) YIELD n_components RETURN n_components",
        lambda rows: {"components": int(rows[0][0]) if rows else None},
        {"implementation": "wcc.get_components (Python module); isolated nodes are in the vertex list"})
    add("scc", "nxalg.strongly_connected_components",
        "CALL nxalg.strongly_connected_components() YIELD components RETURN size(components)",
        lambda rows: {"components": int(rows[0][0]) if rows else None},
        {"implementation": "nxalg.strongly_connected_components (networkx inside Memgraph); counts components of size >= 1"})
    add("betweenness_sampled200", "nxalg.betweenness_centrality",
        f"CALL nxalg.betweenness_centrality({refalgos.BETWEENNESS_SAMPLE}, false, null, false, 42) YIELD node, betweenness "
        f"RETURN node.{prop} AS id, betweenness",
        _scores, {"implementation": "nxalg.betweenness_centrality (networkx inside Memgraph, DIRECTED graph)", "sampled": True, "sample_size": 200, "seed": 42})
    if len(graph.nodes) <= refalgos.DEFAULT_EXACT_MAX_NODES["networkx"]:
        add("betweenness_exact", "nxalg.betweenness_centrality",
            f"CALL nxalg.betweenness_centrality(null, false, null, false, null) YIELD node, betweenness "
            f"RETURN node.{prop} AS id, betweenness",
            _scores, {"implementation": "nxalg.betweenness_centrality (networkx inside Memgraph, DIRECTED graph)", "sampled": False})
    for algo in ("closeness_harmonic", "louvain", "leiden", "triangle_count"):
        legs[algo] = (None, None, {"skipped": "no native or bundled procedure in this Memgraph image (needs the MAGE image)"})
    return legs


# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------


def _pearson_vs_reference(refs: Dict[str, Dict[str, float]], algo: str, scores: Dict[str, float]) -> Optional[float]:
    base = algo if algo in refs else None
    if base is None and algo.startswith("betweenness"):
        base = "betweenness_exact" if "betweenness_exact" in refs else None
    if base is None:
        return None
    return round(refalgos.pearson(scores, refs[base]), 4)


def _reference_scores(graph: datasets.Graph, algos: List[str]) -> Dict[str, Dict[str, float]]:
    """igraph scores for the algorithms whose output is a score per node, to correlate each engine against."""
    out: Dict[str, Dict[str, float]] = {}
    try:
        ref = refalgos.Reference(graph, "igraph")
        legs = ref.legs()
        for algo in ("pagerank", "degree_out", "degree_both", "betweenness_exact", "closeness_harmonic"):
            if algo in algos and legs.get(algo, (None,))[0] is not None:
                out[algo] = legs[algo][0]()["scores"]
    except Exception as exc:  # the cross-check is a convenience, never a reason to stop a run
        print(f"  (reference scores unavailable: {exc})", file=sys.stderr)
    return out


def _time_leg(call: Callable[[], Any], warmup: int, runs: int):
    return time_runs(call, warmup=warmup, runs=runs, catch=True)


def record_leg(out: Path, engine: str, algo: str, values: List[float], last: Any, params: Dict[str, Any],
               summarize: Optional[Callable], refs: Dict[str, Dict[str, float]], log=print) -> None:
    p = dict(params)
    if isinstance(last, Exception):
        p["error"] = f"{type(last).__name__}: {str(last)[:300]}"
        log(f"  {engine:8s} {algo:24s} ERROR {p['error'][:100]}")
    else:
        summary = summarize(last) if summarize else (last if isinstance(last, dict) else {})
        scores = summary.pop("scores", None)
        if scores is not None:
            p["nodes_scored"] = len(scores)
            if refs:
                pr = _pearson_vs_reference(refs, algo, scores)
                if pr is not None:
                    p["pearson_vs_igraph"] = pr
        p.update(summary)
        med = sorted(values)[len(values) // 2] if values else float("nan")
        extra = {k: v for k, v in p.items() if k in ("communities", "components", "triangles", "pearson_vs_igraph")}
        log(f"  {engine:8s} {algo:24s} n={len(values):2d} p50={med:10.3f} ms  {extra}")
    write_record(out, make_record(engine, "algos", algo, values, p))


def run_database(eng: Engine, graph: datasets.Graph, algos: List[str], args, base: Dict[str, Any]) -> None:
    refs = {} if args.no_check else _reference_scores(graph, algos)
    if eng.name == "neo4j":
        if eng.gds_version() is None:
            raise SystemExit("neo4j: GDS is not installed (competitors.sh gds, then restart); use --engine networkx/igraph")
        gver = eng.gds_version()
        nat, und = f"ivg_nat_{graph.name}", f"ivg_und_{graph.name}"
        for name in (nat, und):
            eng.run(f"CALL gds.graph.drop('{name}', false) YIELD graphName RETURN graphName")
        lab = graph.label
        t0 = time.perf_counter()
        eng.run(f"CALL gds.graph.project('{nat}', '{lab}', '*') YIELD graphName RETURN graphName")
        t_nat = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        eng.run(f"CALL gds.graph.project('{und}', '{lab}', {{ALL: {{type: '*', orientation: 'UNDIRECTED'}}}}) YIELD graphName RETURN graphName")
        t_und = (time.perf_counter() - t0) * 1000
        write_record(args.out, make_record("neo4j", "algos", "gds_project_natural", [t_nat], {**base, "gds": gver}))
        write_record(args.out, make_record("neo4j", "algos", "gds_project_undirected", [t_und], {**base, "gds": gver}))
        legs = gds_legs(graph, nat, und)
        try:
            for algo in algos:
                if algo not in legs:
                    reason = "exact algorithm on a graph above the GDS guard" if algo in ("betweenness_exact", "closeness_harmonic") else "no GDS procedure"
                    write_record(args.out, make_record("neo4j", "algos", algo, [], {**base, "skipped": f"{reason} ({len(graph.nodes)} nodes)"}))
                    print(f"  neo4j    {algo:24s} skipped: {reason}")
                    continue
                cypher, summ, params = legs[algo]
                params = {**base, **params, "gds": gver, "cypher": cypher}
                values, last = _time_leg(lambda c=cypher: eng.run(c, None, args.timeout_s), args.warmup, args.runs)
                if isinstance(last, Exception):
                    eng.reset_session()
                record_leg(args.out, "neo4j", algo, values, last, params, summ, refs)
        finally:
            for name in (nat, und):
                try:
                    eng.run(f"CALL gds.graph.drop('{name}', false) YIELD graphName RETURN graphName")
                except Exception:
                    pass
    else:
        legs_m = memgraph_legs(graph, eng.procedures())
        for algo in algos:
            if algo not in legs_m:
                continue
            cypher, summ, params = legs_m[algo]
            if cypher is None:
                write_record(args.out, make_record("memgraph", "algos", algo, [], {**base, **params}))
                print(f"  memgraph {algo:24s} skipped: {params['skipped']}")
                continue
            params = {**base, **params, "cypher": cypher}
            values, last = _time_leg(lambda c=cypher: eng.run(c), args.warmup, args.runs)
            if isinstance(last, Exception):
                eng.reset_session()
            record_leg(args.out, "memgraph", algo, values, last, params, summ, refs)


def run_reference(impl: str, graph: datasets.Graph, algos: List[str], args, base: Dict[str, Any]) -> None:
    if impl == "networkx" and len(graph.edges) > args.ref_max_edges:
        write_record(args.out, make_record(impl, "algos", "build_graph", [], {**base, "skipped": f"{len(graph.edges)} edges exceed --ref-max-edges {args.ref_max_edges}"}))
        print(f"  networkx skipped: {len(graph.edges)} edges exceed --ref-max-edges {args.ref_max_edges}")
        return
    t0 = time.perf_counter()
    ref = refalgos.Reference(graph, impl, exact_max_nodes=args.ref_max_nodes)
    write_record(args.out, make_record(impl, "algos", "build_graph", [(time.perf_counter() - t0) * 1000], base))
    legs = ref.legs()
    refs: Dict[str, Dict[str, float]] = {}
    for algo in algos:
        call, params = legs.get(algo, (None, {"skipped": f"{impl} has no {algo}"}))
        if call is None:
            write_record(args.out, make_record(impl, "algos", algo, [], {**base, **params}))
            print(f"  {impl:8s} {algo:24s} skipped: {params.get('skipped')}")
            continue
        values, last = _time_leg(call, args.warmup, args.runs)
        record_leg(args.out, impl, algo, values, last, {**base, **params}, None, refs)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", required=True, help="neo4j, memgraph, networkx, igraph, a comma list, or all")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--algos", help=f"comma list; default all of {','.join(refalgos.ALGOS)}")
    ap.add_argument("--warmup", type=int, default=3)
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--timeout-s", type=float, default=600.0)
    ap.add_argument("--ref-max-nodes", type=int, default=None, help="exact betweenness/closeness guard for the references")
    ap.add_argument("--ref-max-edges", type=int, default=2_000_000, help="skip networkx above this many edges")
    ap.add_argument("--no-check", action="store_true", help="skip the Pearson cross-check against igraph")
    ap.add_argument("--neo4j-uri")
    ap.add_argument("--memgraph-uri")
    args = ap.parse_args(argv)

    graph = datasets.get_graph(args.dataset)
    algos = args.algos.split(",") if args.algos else list(refalgos.ALGOS)
    wanted = ALGO_ENGINES if args.engine == "all" else args.engine.split(",")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    base = {"dataset": graph.name, "nodes": len(graph.nodes), "edges": len(graph.edges), "warmup": args.warmup, "runs": args.runs}
    meta_path = args.out.with_suffix(args.out.suffix + ".meta.json")
    meta: Dict[str, Any] = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    meta.setdefault("algos", {})[graph.name] = {"warmup": args.warmup, "runs": args.runs, "host": platform.platform(),
                                                 "written": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    rc = 0
    for name in wanted:
        print(f"{name} / {graph.name} ({len(graph.nodes)} nodes, {len(graph.edges)} edges)")
        if name in ("neo4j", "memgraph"):
            eng = engines.connect(name, uri=args.neo4j_uri if name == "neo4j" else args.memgraph_uri)
            try:
                if eng.node_count(graph.label) != len(graph.nodes):
                    print(f"{name}: database does not hold dataset {graph.name}; start its volume and load it", file=sys.stderr)
                    rc = 2
                    continue
                meta["algos"][graph.name][name] = {"version": eng.version(), "gds": eng.gds_version()}
                run_database(eng, graph, algos, args, base)
            finally:
                eng.close()
        elif name in ("networkx", "igraph"):
            import importlib.metadata as md

            meta["algos"][graph.name][name] = {"version": md.version("networkx" if name == "networkx" else "python-igraph")}
            run_reference(name, graph, algos, args, base)
        else:
            raise SystemExit(f"unknown engine {name!r}")
    meta_path.write_text(json.dumps(meta, indent=2))
    return rc


if __name__ == "__main__":
    sys.exit(main())
