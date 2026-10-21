"""IVG half of the LDBC SNB benchmark: load the raw CSVs into one side's namespace, then run the query set.

Runs as a script under either side's tree (PYTHONPATH=<tree>), so it uses only the stdlib, `iris` and that tree's engine:

    PYTHONPATH=/tmp/ivg-src-A python scripts/bench/alt/snb_worker.py load  --ns SNBBENCHA --root /tmp/snb1/out --out load.json
    PYTHONPATH=/tmp/ivg-src-A python scripts/bench/alt/snb_worker.py query --ns SNBBENCHA --root /tmp/snb1/out --out q.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
from pathlib import Path

try:
    from scripts.bench.alt import snb_model as model
    from scripts.bench.alt import snb_queries as queries
    from scripts.bench.alt import snb_updates as updates
    from scripts.bench.alt import snb_weights as weights
except ImportError:
    import snb_model as model
    import snb_queries as queries
    import snb_updates as updates
    import snb_weights as weights

# The dimension setup_sides.py created the namespaces with; initialize_schema refuses to run without one.
EMBEDDING_DIM = 768


def node_batches(nodes, size=20_000):
    batch = []
    for n in nodes:
        batch.append({"id": model.node_key(n["label"], n["id"]), "labels": [n["label"]], "properties": n["props"]})
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def edge_batches(edges, size=50_000):
    batch = []
    for e in edges:
        batch.append({"s": model.node_key(*e["src"]), "p": e["type"], "o": model.node_key(*e["dst"])})
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _norm(v):
    if isinstance(v, float):
        v = round(v, 6)  # costs summed in a different order differ in the last bits
        if v == int(v):
            return int(v)
    return v


def signature(rows):
    """Order-insensitive digest of a result set; 1.0 and 1 compare equal (engines differ on numeric types)."""
    parts = sorted(json.dumps([_norm(c) for c in row], default=str, sort_keys=True) for row in rows)
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def plan_leg(first_ms, params, warmup, reps):
    """(parameter sets, warm-up passes, timed passes): a slow first call shrinks the leg so it still finishes."""
    if first_ms <= 1_000:
        return params, warmup, reps
    if first_ms <= 10_000:
        return min(params, 5), 1, 1
    return 1, 1, 2


def rows_or_raise(res):
    """The rows of an engine result. execute_cypher returns an empty result with `.error` set when the database rejects the
    statement, which a timing loop would record as a fast zero-row answer; here it is an error."""
    err = getattr(res, "error", None)
    if err:
        raise RuntimeError(str(err)[:300])
    return res.rows if hasattr(res, "rows") else res["rows"]


def parse_plan(text):
    return tuple(int(x) for x in text.split(",")) if text else None


def choose_plan(first_ms, params, warmup, reps, forced=None):
    """The adaptive plan, unless the caller forces one (side B repeats side A's plan so both run the same parameter sets)."""
    if forced:
        return forced
    plan = plan_leg(first_ms, params, warmup, reps)
    cap = parse_plan(os.environ.get("IVG_SNB_PLAN_CAP"))  # a ceiling for the largest scale, where every call can take minutes
    return tuple(min(p, c) for p, c in zip(plan, cap)) if cap else plan


def call_with_cap(fn, cap_s):
    """(finished, value). A call that outlives the cap is abandoned; the caller then exits the process to drop its connection."""
    import threading

    box = {}

    def run():
        try:
            box["v"] = fn()
        except Exception as ex:  # surfaced to the caller as a finished call with the exception as value
            box["v"] = ex

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(cap_s)
    if t.is_alive():
        return False, None
    return True, box.get("v")


def _engine(ns):
    import iris
    from iris_vector_graph.engine import IRISGraphEngine

    conn = iris.connect(
        os.environ.get("IRIS_HOST", "localhost"), int(os.environ.get("IRIS_PORT", "31972")), ns,
        os.environ.get("IRIS_USERNAME", "_SYSTEM"), os.environ.get("IRIS_PASSWORD", "SYS"),
    )
    return conn, IRISGraphEngine(conn, embedding_dimension=EMBEDDING_DIM)


def run_load(args):
    conn, engine = _engine(args.ns)
    phases = {}
    t0 = time.perf_counter()
    n_nodes = 0
    for batch in node_batches(model.iter_nodes(args.root)):
        engine.bulk_create_nodes(batch)
        n_nodes += len(batch)
        print(f"  nodes {n_nodes:,}  {time.perf_counter() - t0:.0f}s", flush=True)
    phases["nodes_s"] = round(time.perf_counter() - t0, 2)
    # A fresh engine has not looked at the namespace: capabilities.objectscript_deployed stays False until initialize_schema runs,
    # and bulk_ingest_edges then takes its per-row Python fallback (1.5K edges/s against 9-20K with the server-side loader). The
    # nodes above are loaded before that on purpose: the fallback disables the indexes around the batch, whereas the server-side node
    # loader writes each property row through the full-text index and ran at about 40 nodes/s on this data.
    engine.initialize_schema()
    t1 = time.perf_counter()
    n_edges = 0
    with engine.bulk_load_session() as session:
        for batch in edge_batches(model.iter_edges(args.root)):
            session.add_edges(batch)
            n_edges += len(batch)
            print(f"  edges {n_edges:,}  {time.perf_counter() - t1:.0f}s", flush=True)
    phases["edges_s"] = round(time.perf_counter() - t1, 2)
    phases["session_stats"] = dict(session.stats)
    # A bulk load leaves the optimizer with the statistics of the empty tables (or none): the same SQL planned 40x differently in
    # two otherwise identical namespaces. Tune every table the same way before anything is timed.
    from iris_vector_graph.schema import GraphSchema

    t2 = time.perf_counter()
    phases["tuned"] = GraphSchema.tune_tables(conn.cursor())
    conn.commit()
    phases["tune_s"] = round(time.perf_counter() - t2, 2)
    phases["total_s"] = round(time.perf_counter() - t0, 2)
    out = {"ns": args.ns, "root": args.root, "nodes": n_nodes, "edges": n_edges, "phases": phases}
    Path(args.out).write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, default=str))


def ivg_counts(conn, labels, etypes):
    """Rows per label and per edge predicate straight from the tables (not through the Cypher under test)."""
    cur = conn.cursor()
    out = {"nodes": {}, "edges": {}}
    for label in labels:
        cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE label = ?", [label])
        out["nodes"][label] = int(cur.fetchone()[0])
    for etype in etypes:
        cur.execute("SELECT COUNT(*) FROM Graph_KG.rdf_edges WHERE p = ?", [etype])
        out["edges"][etype] = int(cur.fetchone()[0])
    return out


def detect_capabilities(conn, engine):
    """What initialize_schema() would detect about the deployed classes, without its schema work (on a loaded SF1 graph that work
    builds the full-text index on 25M property rows)."""
    from iris_vector_graph.schema import GraphSchema

    engine.capabilities = GraphSchema.check_objectscript_classes(conn.cursor(), conn)
    return engine.capabilities


def run_update(args):
    """Time each interactive update (and the delete that undoes it) as its own call; read the tables back after each phase."""
    flag = Path(os.environ.get("IVG_BENCH_HOME", str(Path.home() / ".cache" / "ivg-bench"))) / f"skip_updates_{args.ns.rstrip('AB')}"
    if flag.exists():
        Path(args.out).write_text(json.dumps({"ns": args.ns, "n": args.n, "skipped": flag.read_text().strip(), "results": {}}, indent=2))
        print("skipped:", flag.read_text().strip(), flush=True)
        sys.stdout.flush()
        os._exit(0)
    conn, engine = _engine(args.ns)
    detect_capabilities(conn, engine)
    params = updates.update_params(updates.existing_ids(args.root), n=args.n, seed=args.seed, id_offset=args.id_offset, taken=updates.existing_pairs(args.root))
    delta = updates.expected_delta(params)
    before = ivg_counts(conn, delta["nodes"], delta["edges"])
    results = {}

    def phase(statements):
        for name, sets in statements.items():
            spec = {**updates.UPDATES, **updates.DELETES}[name]
            text = updates.statement(name, "ivg")
            times, err = [], None
            for ps in sets:
                ps = updates.with_key(ps, spec["keyed"]) if spec.get("keyed") else ps
                t = time.perf_counter()
                try:
                    res = engine.execute_cypher(text, ps)
                except Exception as ex:  # a statement the engine rejects ends that update type, not the run
                    err = f"{type(ex).__name__}: {str(ex)[:280]}"
                    break
                ms = (time.perf_counter() - t) * 1000
                if getattr(res, "error", None):
                    err = str(res.error)[:300]
                    break
                times.append(ms)
            results[name] = {"values_ms": times, "error": err}
            print(f"  {name:26s} n={len(times):4d} p50={statistics.median(times) if times else float('nan'):9.3f}ms {err or ''}", flush=True)

    phase(params)
    mid = ivg_counts(conn, delta["nodes"], delta["edges"])
    grew = {k: {x: mid[k][x] - before[k][x] for x in mid[k]} for k in mid}
    phase(updates.delete_params(params))
    after = ivg_counts(conn, delta["nodes"], delta["edges"])
    out = {"ns": args.ns, "n": args.n, "results": results, "expected": delta, "grew": grew, "ok": grew == delta, "restored": after == before}
    Path(args.out).write_text(json.dumps(out, indent=2))
    print("grew as expected" if out["ok"] else f"MISMATCH grew={grew} expected={delta}", flush=True)
    print("deletes restored the graph" if out["restored"] else f"NOT RESTORED before={before} after={after}", flush=True)
    sys.stdout.flush()
    os._exit(0)


def run_weights(args):
    """Write the interaction weights onto the KNOWS adjacency (what the weighted-path call reads), then check one path."""
    conn, engine = _engine(args.ns)
    detect_capabilities(conn, engine)
    iris_obj = engine._iris_obj()
    t0 = time.perf_counter()
    n = 0
    for (a, b), w in weights.knows_weights(args.root).items():
        iris_obj.classMethodVoid("Graph.KG.EdgeScan", "WriteAdjacency", model.node_key("Person", a), "KNOWS", model.node_key("Person", b), repr(w), 0)
        n += 1
    out = {"ns": args.ns, "edges": n, "seconds": round(time.perf_counter() - t0, 2)}
    for k, plist in enumerate(queries.sample_params(args.root, n=3, seed=42)):
        res = engine.execute_cypher(weights.inline(queries.ivg_cypher(queries.QUERIES["BIW_weighted_shortest_path"]), plist), plist)
        out.setdefault("check_rows", []).append([r[1] for r in rows_or_raise(res)])
    Path(args.out).write_text(json.dumps(out, indent=2))
    print(json.dumps(out), flush=True)
    sys.stdout.flush()
    os._exit(0)


def query_params(args):
    """The parameter sets to time: an earlier run's ("params" in its JSON, --params-from), so both runs compare set by
    set, or a fresh sample of the dataset."""
    if getattr(args, "params_from", None):
        return json.loads(Path(args.params_from).read_text())["params"]
    return queries.sample_params(args.root, n=args.params, seed=args.seed)


def run_query(args):
    conn, engine = _engine(args.ns)
    plist = query_params(args)
    results = {}
    names = args.only or list(queries.QUERIES)
    for name in names:
        spec = queries.QUERIES[name]
        cypher = queries.ivg_cypher(spec)
        keep = spec.get("ivg_client_limit")
        sets = [{k: p[k] for k in spec["params"]} for p in plist] if spec["params"] else [{}]
        sigs, counts, times, err, plan = [], [], [], None, None

        def one(ps):
            t = time.perf_counter()
            res = engine.execute_cypher(weights.inline(cypher, ps) if spec.get("ivg_inline") else cypher, ps)
            rows = rows_or_raise(res)
            if "ivg_pick" in spec:  # the procedure returns (path, totalCost) whatever the RETURN names
                rows = [[r[spec["ivg_pick"]]] for r in rows]
            if keep:
                rows = rows[:keep]
            return rows, (time.perf_counter() - t) * 1000

        finished, first = call_with_cap(lambda: one(sets[0]), args.cap_s)
        if not finished:
            err = f"timeout: first call still running after {args.cap_s:.0f}s"
        elif isinstance(first, Exception):
            err = f"{type(first).__name__}: {str(first)[:300]}"
        else:
            n_sets, warmup, reps = choose_plan(first[1], len(sets), args.warmup, args.reps, parse_plan(args.plan))
            plan = [n_sets, warmup, reps]
            sets = sets[:n_sets]
            try:
                for rnd in range(warmup + reps):
                    for i, ps in enumerate(sets):
                        rows, ms = first if (rnd == 0 and i == 0) else one(ps)
                        if rnd >= warmup:
                            times.append(ms)
                        if rnd == 0:
                            sigs.append(signature(rows))
                            counts.append(len(rows))
            except Exception as ex:  # an unsupported query is a result, not a crash
                err = f"{type(ex).__name__}: {str(ex)[:300]}"
        results[name] = {"values_ms": times, "rows": counts, "signatures": sigs, "error": err, "plan": plan}
        if err:
            print(f"  {name:28s} ERROR {err[:120]}", flush=True)
        else:
            print(f"  {name:28s} n={len(times):4d} p50={statistics.median(times):9.3f}ms", flush=True)
        if err and err.startswith("timeout"):
            break
    Path(args.out).write_text(json.dumps({"ns": args.ns, "params": plist, "results": results}, indent=2))
    sys.stdout.flush()
    os._exit(0)  # a call abandoned by the cap may still be running on the server; exiting drops the connection


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("load", "query"):
        p = sub.add_parser(name)
        p.add_argument("--ns", required=True)
        p.add_argument("--root", required=True)
        p.add_argument("--out", required=True)
        if name == "query":
            p.add_argument("--params", type=int, default=20)
            p.add_argument("--seed", type=int, default=42)
            p.add_argument("--warmup", type=int, default=1)
            p.add_argument("--reps", type=int, default=3)
            p.add_argument("--only", nargs="+")
            p.add_argument("--plan", help="n_params,warmup,reps to use instead of the adaptive plan")
            p.add_argument("--cap-s", type=float, default=60.0, help="a first call slower than this ends the leg as a timeout")
            p.add_argument("--params-from", help="time the parameter sets an earlier run's JSON recorded (its \"params\")")
    pw = sub.add_parser("weights")
    pw.add_argument("--ns", required=True)
    pw.add_argument("--root", required=True)
    pw.add_argument("--out", required=True)
    pu = sub.add_parser("update")
    pu.add_argument("--ns", required=True)
    pu.add_argument("--root", required=True)
    pu.add_argument("--out", required=True)
    pu.add_argument("--n", type=int, default=200)
    pu.add_argument("--seed", type=int, default=42)
    pu.add_argument("--id-offset", type=int, default=0, help="shift the new ids so a smoke run and a real run never create the same node")
    args = ap.parse_args(argv)
    {"load": run_load, "query": run_query, "update": run_update, "weights": run_weights}[args.cmd](args)


if __name__ == "__main__":
    main()
