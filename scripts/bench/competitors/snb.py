#!/usr/bin/env python3
"""LDBC SNB on Neo4j and Memgraph: load the same raw CSVs the IVG sides load, then run the same query set.

    python snb.py load  --engine neo4j --root /tmp/snb1/out --out results/snb_neo4j_load.jsonl
    python snb.py query --engine neo4j --root /tmp/snb1/out --out results/snb_neo4j.jsonl --warmup 1 --reps 3
"""

from __future__ import annotations

import argparse
import logging
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))  # repo root: scripts.bench.alt.* (stdlib-only modules)

import engines  # noqa: E402
from jsonl import make_record, write_record  # noqa: E402
from scripts.bench.alt import snb_model as model  # noqa: E402
from scripts.bench.alt import snb_queries as queries  # noqa: E402
from scripts.bench.alt import snb_updates as updates  # noqa: E402
from scripts.bench.alt import snb_weights as weights  # noqa: E402
from scripts.bench.alt import snb_worker  # noqa: E402  (signature only; stdlib at import time)


def node_statement(label: str) -> str:
    return f"UNWIND $rows AS r CREATE (n:{label}) SET n = r"


def edge_statement(etype: str, slabel: str, tlabel: str) -> str:
    return f"UNWIND $rows AS r MATCH (a:{slabel} {{sid: r.s}}), (b:{tlabel} {{sid: r.t}}) CREATE (a)-[:{etype}]->(b)"


def index_statement(engine: str, label: str) -> str:
    if engine == "memgraph":
        return f"CREATE INDEX ON :{label}(sid)"
    return f"CREATE INDEX snb_{label.lower()}_id IF NOT EXISTS FOR (n:{label}) ON (n.sid)"


def node_groups(nodes: Iterable[Dict[str, Any]], size: int = 20_000) -> Iterator[Tuple[str, List[Dict[str, Any]]]]:
    """(label, [props]) batches; a batch ends when the label changes or it is full (the stream is label-sorted)."""
    label, buf = None, []
    for n in nodes:
        if n["label"] != label and buf:
            yield label, buf
            buf = []
        label = n["label"]
        buf.append(n["props"])
        if len(buf) >= size:
            yield label, buf
            buf = []
    if buf:
        yield label, buf


def edge_groups(edges: Iterable[Dict[str, Any]], size: int = 20_000) -> Iterator[Tuple[str, str, str, List[Dict[str, str]]]]:
    key, buf = None, []
    for e in edges:
        k = (e["type"], e["src"][0], e["dst"][0])
        if k != key and buf:
            yield (*key, buf)
            buf = []
        key = k
        buf.append({"s": e["src"][1], "t": e["dst"][1]})
        if len(buf) >= size:
            yield (*key, buf)
            buf = []
    if buf:
        yield (*key, buf)


logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)  # one warning per call for properties a query names but a graph lacks


def make_engine(name: str, args) -> engines.Engine:
    uri, auth = (args.neo4j_uri or engines.NEO4J_URI_DEFAULT, engines.NEO4J_AUTH_DEFAULT) if name == "neo4j" else (args.memgraph_uri or engines.MEMGRAPH_URI_DEFAULT, engines.MEMGRAPH_AUTH_DEFAULT)
    return engines.Engine(name, uri, auth)


def run_load(args) -> int:
    eng = make_engine(args.engine, args)
    eng.run("MATCH (n) DETACH DELETE n")
    t0 = time.perf_counter()
    for lab in {x[0] for x in model.NODE_FILES}:
        try:
            eng.run(index_statement(args.engine, lab))
        except Exception as ex:  # an index that already exists
            print("  index:", str(ex)[:100])
    n_nodes = 0
    for label, rows in node_groups(model.iter_nodes(args.root)):
        eng.run(node_statement(label), {"rows": rows})
        n_nodes += len(rows)
    t_nodes = time.perf_counter() - t0
    print(f"  nodes {n_nodes:,} in {t_nodes:.0f}s", flush=True)
    t1 = time.perf_counter()
    n_edges = 0
    for etype, sl, tl, rows in edge_groups(model.iter_edges(args.root)):
        eng.run(edge_statement(etype, sl, tl), {"rows": rows})
        n_edges += len(rows)
        if n_edges % 1_000_000 < len(rows):
            print(f"  edges {n_edges:,} {time.perf_counter() - t1:.0f}s", flush=True)
    t_edges = time.perf_counter() - t1
    print(f"  edges {n_edges:,} in {t_edges:.0f}s", flush=True)
    if args.out:
        write_record(Path(args.out), make_record(args.engine, "snb", "load_total", [(time.perf_counter() - t0) * 1000], {"nodes": n_nodes, "edges": n_edges, "nodes_s": round(t_nodes, 1), "edges_s": round(t_edges, 1)}))
    eng.close()
    return 0


def run_query(args) -> int:
    eng = make_engine(args.engine, args)
    plist = queries.sample_params(args.root, n=args.params, seed=args.seed)
    for name in args.only or list(queries.QUERIES):
        spec = queries.QUERIES[name]
        sets = [{k: p[k] for k in spec["params"]} for p in plist] if spec["params"] else [{}]
        times, sigs, counts, err = [], [], [], None
        try:
            for rnd in range(args.warmup + args.reps):
                for ps in sets:
                    t = time.perf_counter()
                    rows = eng.run(spec.get(args.engine, spec["cypher"]), ps, timeout_s=args.timeout_s)
                    ms = (time.perf_counter() - t) * 1000
                    if rnd >= args.warmup:
                        times.append(ms)
                    if rnd == 0:
                        sigs.append(snb_worker.signature([list(r.values()) if hasattr(r, "values") else list(r) for r in rows]))
                        counts.append(len(rows))
        except Exception as ex:
            err = f"{type(ex).__name__}: {str(ex)[:300]}"
            times = []
        if err:
            print(f"  {name:28s} ERROR {err[:120]}", flush=True)
        else:
            print(f"  {name:28s} n={len(times):4d} p50={statistics.median(times):9.3f}ms", flush=True)
        rec = make_record(args.engine, "snb", name, times, {"rows": counts, "signatures": sigs, "error": err, "reps": args.reps, "params": len(sets)})
        if args.out:
            write_record(Path(args.out), rec)
    eng.close()
    return 0


def _counts(eng, labels, etypes) -> Dict[str, Dict[str, int]]:
    out = {"nodes": {}, "edges": {}}
    for label in labels:
        out["nodes"][label] = int(eng.run(f"MATCH (n:{label}) RETURN count(n) AS c")[0]["c"])
    for etype in etypes:
        out["edges"][etype] = int(eng.run(f"MATCH ()-[r:{etype}]->() RETURN count(r) AS c")[0]["c"])
    return out


def run_update(args) -> int:
    """The interactive updates and the deletes that undo them, one call each (a transaction), with the same parameters the IVG sides get."""
    eng = make_engine(args.engine, args)
    params = updates.update_params(updates.existing_ids(args.root), n=args.n, seed=args.seed, id_offset=args.id_offset, taken=updates.existing_pairs(args.root))
    delta = updates.expected_delta(params)
    before = _counts(eng, delta["nodes"], delta["edges"])

    def phase(statements):
        for name, sets in statements.items():
            text = updates.statement(name, args.engine)
            times, err = [], None
            try:
                for ps in sets:
                    t = time.perf_counter()
                    eng.run(text, ps, timeout_s=args.timeout_s)
                    times.append((time.perf_counter() - t) * 1000)
            except Exception as ex:
                err = f"{type(ex).__name__}: {str(ex)[:300]}"
            print(f"  {name:26s} n={len(times):4d} p50={statistics.median(times) if times else float('nan'):9.3f}ms {err or ''}", flush=True)
            if args.out:
                write_record(Path(args.out), make_record(args.engine, "snb_update", name, times, {"error": err, "n": len(sets)}))

    phase(params)
    mid = _counts(eng, delta["nodes"], delta["edges"])
    grew = {k: {x: mid[k][x] - before[k][x] for x in mid[k]} for k in mid}
    phase(updates.delete_params(params))
    restored = _counts(eng, delta["nodes"], delta["edges"]) == before
    print("grew as expected" if grew == delta else f"MISMATCH grew={grew} expected={delta}", flush=True)
    print("deletes restored the graph" if restored else "NOT RESTORED", flush=True)
    if args.out:
        write_record(Path(args.out), make_record(args.engine, "snb_update", "grew", [0.0], {"grew": grew, "expected": delta, "ok": grew == delta, "restored": restored}))
    eng.close()
    return 0


def run_weights(args) -> int:
    """Put the interaction weights on KNOWS and, on Neo4j, project the graph GDS's Dijkstra reads."""
    eng = make_engine(args.engine, args)
    rows = [{"s": a, "t": b, "w": w} for (a, b), w in weights.knows_weights(args.root).items()]
    t0 = time.perf_counter()
    for i in range(0, len(rows), 5_000):
        eng.run("UNWIND $rows AS r MATCH (a:Person {sid: r.s})-[k:KNOWS]->(b:Person {sid: r.t}) SET k.weight = r.w", {"rows": rows[i:i + 5_000]})
    if args.engine == "neo4j":
        try:
            eng.run("CALL gds.graph.drop('snbw', false)")
        except Exception:
            pass
        eng.run("CALL gds.graph.project('snbw', 'Person', {KNOWS: {orientation: 'UNDIRECTED', properties: 'weight'}})")
    n = eng.run("MATCH ()-[k:KNOWS]->() WHERE k.weight IS NOT NULL RETURN count(k) AS c")[0]["c"]
    print(f"weights on {n:,} of {len(rows):,} KNOWS edges in {time.perf_counter() - t0:.1f}s", flush=True)
    eng.close()
    return 0 if n == len(rows) else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["load", "query", "update", "weights"])
    ap.add_argument("--engine", choices=["neo4j", "memgraph"], required=True)
    ap.add_argument("--root", default="/tmp/snb1/out")
    ap.add_argument("--out")
    ap.add_argument("--params", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--timeout-s", type=float, default=120.0)
    ap.add_argument("--only", nargs="+")
    ap.add_argument("--n", type=int, default=200, help="update: statements per update type")
    ap.add_argument("--id-offset", type=int, default=0)
    ap.add_argument("--neo4j-uri")
    ap.add_argument("--memgraph-uri")
    args = ap.parse_args(argv)
    return {"load": run_load, "query": run_query, "update": run_update, "weights": run_weights}[args.step](args)


if __name__ == "__main__":
    raise SystemExit(main())
