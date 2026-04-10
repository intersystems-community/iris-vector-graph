#!/usr/bin/env python3
"""
Benchmark: v1.39.0 pre-aggregated temporal analytics.
Datasets: ~/ws/iris-datasets/re2-tt/{traces.tsv,rcaeval.tsv}
TSV format: timestamp<TAB>source<TAB>predicate<TAB>target<TAB>weight
"""
import argparse, json, os, statistics, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import iris
from iris_devtester import IRISContainer
c = IRISContainer.attach("iris-vector-graph-main")
conn = iris.connect(c.get_container_host_ip(), int(c.get_exposed_port(1972)), "USER", "_SYSTEM", "SYS")
iriso = iris.createIRIS(conn)
from iris_vector_graph.engine import IRISGraphEngine
engine = IRISGraphEngine(conn)

DATASET_DIR = os.path.expanduser("~/ws/iris-datasets/re2-tt")
TRACES_TSV  = os.path.join(DATASET_DIR, "traces.tsv")
RCAEVAL_TSV = os.path.join(DATASET_DIR, "rcaeval.tsv")
INGEST_BATCH = 500
WARMUP, ROUNDS, QUERY_REPS = 2, 5, 12

def purge():
    iriso.classMethodVoid("Graph.KG.TemporalIndex", "Purge")

def bulk_batched(edges):
    total = 0
    for i in range(0, len(edges), INGEST_BATCH):
        total += engine.bulk_create_edges_temporal(edges[i:i+INGEST_BATCH])
    return total

def ingest_tsv(path, limit=None):
    edges = []
    with open(path) as f:
        for i, line in enumerate(f):
            if limit and i >= limit: break
            p = line.strip().split("\t")
            if len(p) < 5: continue
            try:
                edges.append({"ts": int(p[0]), "s": p[1], "p": p[2], "o": p[3], "w": float(p[4])})
            except (ValueError, IndexError):
                continue
    return edges

def bench_ingest(edges):
    rates = []
    for r in range(WARMUP + ROUNDS):
        purge()
        t0 = time.perf_counter_ns()
        bulk_batched(edges)
        t1 = time.perf_counter_ns()
        rate = len(edges) / ((t1 - t0) / 1e9)
        if r >= WARMUP: rates.append(rate)
    purge()
    return rates

def bench_queries(src, pred, ts_min, ts_max):
    results = {}
    wins = {"1b_5min": (ts_max-300, ts_max), "12b_1hr": (ts_max-3600, ts_max), "288b_24hr": (ts_max-86400, ts_max)}
    for fn_name, fn in [("GetAggregate", lambda t0,t1: engine.get_temporal_aggregate(src, pred, "avg", t0, t1)),
                        ("GetBucketGroups", lambda t0,t1: engine.get_bucket_groups(pred, t0, t1)),
                        ("GetDistinctCount", lambda t0,t1: engine.get_distinct_count(src, pred, t0, t1))]:
        for win, (t0w, t1w) in wins.items():
            lats = []
            for _ in range(WARMUP + QUERY_REPS):
                t0 = time.perf_counter_ns(); fn(t0w, t1w); t1 = time.perf_counter_ns()
                lats.append((t1-t0)/1e6)
            results[f"{fn_name}_{win}"] = round(statistics.median(lats[WARMUP:]), 4)
    return results

def bench_hll():
    results = {}
    now = int(time.time())
    for exact in [1, 10, 50, 100, 500, 1000]:
        purge()
        bulk_batched([{"s":"hll:src","p":"CALLS_AT","o":f"tgt:{i}","ts":now+i} for i in range(exact)])
        est = engine.get_distinct_count("hll:src","CALLS_AT",now-10,now+exact+10)
        results[exact] = {"estimate": est, "error_pct": round(abs(est-exact)/exact*100 if exact else 0, 1)}
    purge()
    return results

SEP = "=" * 62
print(SEP)
print("  iris-vector-graph v1.39.0  Pre-Aggregation Benchmark")
print(SEP)

# INGEST
print("\n▶  INGEST  (INGEST_BATCH=500, 5 rounds after 2 warmup)\n")
results_ingest = {}
for label, path, limit in [("traces.tsv CALLS_AT", TRACES_TSV, 200_000),
                            ("rcaeval.tsv EMITS_METRIC_AT", RCAEVAL_TSV, 200_000),
                            ("synthetic 50K", None, 50_000)]:
    if path:
        if not os.path.exists(path):
            print(f"  SKIP {label} — file not found"); continue
        print(f"  Loading {limit:,} rows from {os.path.basename(path)}...")
        edges = ingest_tsv(path, limit=limit)
        print(f"  Parsed {len(edges):,} valid edges")
    else:
        now = int(time.time())
        edges = [{"s":f"svc:{i%27}","p":"CALLS_AT","o":f"svc:{(i+1)%27}","ts":now+i,"w":float(1+i%100)} for i in range(limit)]
    rates = bench_ingest(edges)
    med = statistics.median(rates)
    results_ingest[label] = med
    sc4 = "✅ PASS" if med >= 80_000 else "❌ FAIL"
    print(f"  {label:<38}  {med:>10,.0f} edges/sec  SC-004: {sc4}")
    print(f"  {'':38}  stdev={statistics.stdev(rates):>8,.0f}  all={[f'{r:,.0f}' for r in rates]}\n")

# QUERY LATENCY
print("▶  QUERY LATENCY  (50K edges loaded, median of 12 runs)\n")
print("  Loading 50K-edge query dataset...")
purge()
if os.path.exists(TRACES_TSV):
    qedges = ingest_tsv(TRACES_TSV, limit=50_000)
else:
    now = int(time.time())
    qedges = [{"s":f"svc:q{i%3}","p":"CALLS_AT","o":f"svc:t{i%10}","ts":now-50000+i,"w":float(1+i%100)} for i in range(50_000)]
bulk_batched(qedges)
src  = qedges[0]["s"]
pred = qedges[0]["p"]
ts_min = min(e["ts"] for e in qedges)
ts_max = max(e["ts"] for e in qedges)
span_h = (ts_max - ts_min) / 3600
print(f"  src={src}  pred={pred}  span={span_h:.1f}h  buckets={(ts_max-ts_min)//300:.0f}\n")
qr = bench_queries(src, pred, ts_min, ts_max)
for k, v in qr.items():
    if "GetAggregate" in k and "1b" in k:   sc = "SC-005 <0.5ms"; flag = "✅" if v<0.5 else "❌"
    elif "GetBucketGroups" in k and "1b" in k: sc = "SC-006 <5ms";  flag = "✅" if v<5 else "❌"
    else: sc = ""; flag = "✅" if v < 5 else "⚠️"
    print(f"  {flag} {k:<40s} {v:7.4f}ms  {sc}")
purge()

# HLL ACCURACY
print("\n▶  HLL ACCURACY  (16 registers, expected ~26% error)\n")
print(f"  {'exact':>6}  {'estimate':>8}  {'error %':>8}")
for exact, v in bench_hll().items():
    flag = "✅" if v["error_pct"] <= 35 else "⚠️"
    print(f"  {flag} {exact:>6}  {v['estimate']:>8}  {v['error_pct']:>7.1f}%")

# SUMMARY
print(f"\n{'─'*62}  SUMMARY")
sc4_rate = results_ingest.get("traces.tsv CALLS_AT", results_ingest.get("synthetic 50K", 0))
sc5_val  = qr.get("GetAggregate_1b_5min", 999)
sc6_val  = qr.get("GetBucketGroups_1b_5min", 999)
print(f"  SC-004 ingest ≥80K:       {sc4_rate:>10,.0f}  {'✅ PASS' if sc4_rate>=80_000 else '❌ FAIL'}")
print(f"  SC-005 GetAggregate 1b:   {sc5_val:>10.4f}ms  {'✅ PASS' if sc5_val<0.5 else '❌ FAIL'}")
print(f"  SC-006 GetBucketGroups 1b:{sc6_val:>10.4f}ms  {'✅ PASS' if sc6_val<5.0 else '❌ FAIL'}")
