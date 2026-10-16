"""Exact betweenness and harmonic closeness above the 3,000-node line the earlier legs stopped at.

A seeded random graph of N nodes and mean degree D is loaded into the scratch namespaces ALGOA / ALGOB (one per IVG side), and
the server's all-sources Brandes (`BetweennessGlobalParallel` with the source cap lifted; `engine.betweenness_centrality` always caps at 200 sources) next to the engine's default call, and `closeness_centrality("harmonic")` are timed over interleaved repetitions. The same
edges go to Neo4j GDS, Memgraph and igraph through `scripts/bench/competitors/run_algos.py` (datasets `btw<N>`), and the score
vectors are compared by Pearson correlation.

    python -m scripts.bench.alt.algo_scale setup
    python -m scripts.bench.alt.algo_scale run --n 5000 --deg 6 --reps 3 --out docs/performance/alt-bench/results/algo-scale
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path


def node_id(i):
    return f"a{i}"


def er_edges(n, deg, seed=42):
    """n*deg/2 distinct undirected edges (u < v) drawn uniformly; a G(n, m) graph, the same for every engine."""
    m = n * deg // 2
    rnd = random.Random(seed)
    edges = set()
    while len(edges) < m:
        u, v = rnd.randrange(n), rnd.randrange(n)
        if u != v:
            edges.add((u, v) if u < v else (v, u))
    return sorted(edges)


def summarise(times):
    return {"median_s": statistics.median(times), "min_s": min(times), "max_s": max(times), "reps": len(times), "values_s": list(times)}


def pearson(a, b):
    common = sorted(set(a) & set(b))
    if len(common) < 2:
        return float("nan")
    xs, ys = [a[k] for k in common], [b[k] for k in common]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    if sx == 0 or sy == 0:
        return float("nan")
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def compare_to_reference(scores, ref):
    """How an engine's score vector compares with an exact reference: Pearson (a node it did not score counts as 0), the ratio of the
    means (2 when it counts both directions of an undirected pair) and the number of ids that are not in the graph at all."""
    full = {i: scores.get(i, 0.0) for i in ref}
    mean_ref = statistics.mean(ref.values())
    return {"pearson": pearson(ref, full), "mean_ratio": statistics.mean(full.values()) / mean_ref if mean_ref else float("nan"),
            "scored": len(scores), "foreign_ids": len([i for i in scores if i not in ref])}


def _engine(ns):
    import iris
    from iris_vector_graph.engine import IRISGraphEngine

    conn = iris.connect(os.environ.get("IRIS_HOST", "localhost"), int(os.environ.get("IRIS_PORT", "31972")), ns,
                        os.environ.get("IRIS_USERNAME", "_SYSTEM"), os.environ.get("IRIS_PASSWORD", "SYS"))
    return conn, IRISGraphEngine(conn, embedding_dimension=768)


def run_worker(args):
    """Inside a side's tree: load the graph into the namespace, warm the adjacency, time the two algorithms."""
    conn, engine = _engine(args.ns)
    engine.initialize_schema()
    # Through the engine, which removes the adjacency as well as the rows: a table DELETE leaves ^KG behind, and the next, smaller graph
    # then carried the edges of the bigger one (scores for 19,810 nodes of a 10,000-node graph).
    engine.delete_nodes_by_prefix("a")
    edges = er_edges(args.n, args.deg, args.seed)
    t0 = time.perf_counter()
    engine.bulk_create_nodes([{"id": node_id(i), "labels": ["Node"], "properties": {}} for i in range(args.n)])
    with engine.bulk_load_session() as session:  # one stored direction per edge, both directions written: an undirected graph
        for i in range(0, len(edges), 50_000):
            chunk = edges[i:i + 50_000]
            session.add_edges([{"s": node_id(u), "p": "R", "o": node_id(v)} for u, v in chunk] + [{"s": node_id(v), "p": "R", "o": node_id(u)} for u, v in chunk])
    load_s = time.perf_counter() - t0
    # The adjacency the algorithms read: the Arno accelerator when the container has it (as tests/perf/test_betweenness_vs_gds.py
    # does), the ObjectScript path otherwise; which one ran is in the output.
    iris_obj = engine._iris_obj()
    for method, extra in (("Load", (os.environ.get("IVG_ARNO_SO_PATH", "/tmp/libarno_callout.so"),)), ("WarmAdjCache", ())):
        try:
            iris_obj.classMethodValue("Graph.KG.NKGAccel", method, *extra)
        except Exception:
            pass
    try:
        arno_loaded = bool(int(iris_obj.classMethodValue("Graph.KG.NKGAccel", "IsLoaded")))
    except Exception:
        arno_loaded = False
    out = {"ns": args.ns, "n": args.n, "deg": args.deg, "edges": len(edges), "load_s": round(load_s, 2), "arno_loaded": arno_loaded, "algos": {}}
    def exact_betweenness():
        # engine.betweenness_centrality cannot ask for every source: it passes maxSources=200 and the server caps at that for any
        # sample_size that is 0 or does not change the answer; the server method takes the cap as a parameter, so ask it directly.
        raw = str(iris_obj.classMethodValue("Graph.KG.NKGAccelCentrality", "BetweennessGlobalParallel", 0, 0, 10**9))
        if not raw.startswith("OK:"):
            raise RuntimeError(raw[:200])
        return json.loads(raw[3:])

    legs = {
        "betweenness_exact": exact_betweenness,
        "betweenness_default": lambda: engine.betweenness_centrality(sample_size=0, top_k=0),
        "closeness_harmonic": lambda: engine.closeness_centrality(formula="harmonic", top_k=0),
    }
    scores = {}
    for name, fn in legs.items():
        if args.only and name not in args.only:
            continue
        times, err = [], None
        try:
            for _ in range(args.warmup + args.reps):
                t = time.perf_counter()
                res = fn()
                dt = time.perf_counter() - t
                times.append(dt)
            scores[name] = {r["id"]: float(r["score"]) for r in res}
            times = times[args.warmup:]
        except Exception as ex:
            err, times = f"{type(ex).__name__}: {str(ex)[:300]}", []
        out["algos"][name] = {**(summarise(times) if times else {}), "error": err, "nodes_scored": len(scores.get(name, {}))}
        print(f"  {name:20s} {out['algos'][name].get('median_s', float('nan')):9.3f}s {err or ''}", flush=True)
    Path(args.out).write_text(json.dumps(out, indent=2))
    Path(args.out).with_suffix(".scores.json").write_text(json.dumps(scores))
    sys.stdout.flush()
    os._exit(0)


def run_check(args):
    """Compare every saved IVG score vector with igraph's exact answer on the same edges (needs igraph; run it from the comparison venv)."""
    import igraph

    for path in sorted(Path(args.dir).glob("n*_?_?.scores.json")):
        n = int(path.name.split("_")[0][1:])
        g = igraph.Graph(n=n, edges=er_edges(n, args.deg, args.seed), directed=False)
        ids = [node_id(i) for i in range(n)]
        ref = {"betweenness_exact": dict(zip(ids, g.betweenness(directed=False))),
               "closeness_harmonic": dict(zip(ids, g.harmonic_centrality(normalized=False)))}
        ref["betweenness_default"] = ref["betweenness_exact"]  # the default call is the 200-source approximation of the same quantity
        result = {algo: compare_to_reference(sc, ref[algo]) for algo, sc in json.loads(path.read_text()).items() if algo in ref}
        path.with_name(path.name.replace(".scores.json", ".check.json")).write_text(json.dumps(result, indent=2))
        print(path.name, {k: (round(v["pearson"], 4), v["foreign_ids"]) for k, v in result.items()}, flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("worker")
    w.add_argument("--ns", required=True)
    w.add_argument("--n", type=int, required=True)
    w.add_argument("--deg", type=int, default=6)
    w.add_argument("--seed", type=int, default=42)
    w.add_argument("--reps", type=int, default=3)
    w.add_argument("--warmup", type=int, default=1)
    w.add_argument("--only", nargs="+", help="run only these legs (closeness grows much faster than betweenness)")
    w.add_argument("--out", required=True)
    c = sub.add_parser("check")
    c.add_argument("--dir", required=True)
    c.add_argument("--deg", type=int, default=6)
    c.add_argument("--seed", type=int, default=42)
    args = ap.parse_args(argv)
    {"worker": run_worker, "check": run_check}[args.cmd](args)


if __name__ == "__main__":
    main()
