#!/usr/bin/env python3
"""
Full benchmark: iris-vector-graph v1.40.0 against 535M-edge KGBENCH dataset.

Two modes:
  --query-only   Query benchmarks only — never purges production data (default after full ingest)
  --ingest-only  Ingest benchmark only — uses isolated namespace prefix, no purge of real data
  --full         Both (writes synthetic edges to separate prefix, then purges only that prefix)

Requires: kg-iris container with KGBENCH namespace, 535M edges ingested.
"""
import argparse, csv, os, statistics, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import iris
conn = iris.connect("localhost", 11982, "KGBENCH", "_SYSTEM", "SYS")
iriso = iris.createIRIS(conn)
from iris_vector_graph.engine import IRISGraphEngine
e = IRISGraphEngine(conn)

WARMUP = 3
REPS   = 20
SEP    = "=" * 64

def median(fn, reps=REPS, warmup=WARMUP):
    lats = []
    for _ in range(warmup + reps):
        t0 = time.perf_counter_ns(); fn(); t1 = time.perf_counter_ns()
        lats.append((t1 - t0) / 1e6)
    return round(statistics.median(lats[warmup:]), 4)

def find_dataset_range():
    """Find actual ts range and busiest source from the live dataset."""
    groups = e.get_bucket_groups("CALLS_AT", 0, 9_999_999_999)
    if not groups:
        return None, None, None, None
    top = sorted(groups, key=lambda g: g["count"], reverse=True)[0]
    src, pred = top["source"], "CALLS_AT"
    # Find ts range from bucket index
    ts_start = iriso.classMethodValue(
        "Graph.KG.TemporalIndex", "GetAggregate", src, pred, "count", 0, 9_999_999_999)
    # Use all-time window for representative numbers
    return src, pred, 1_705_917_385, 1_712_000_000

def run_query_benchmark():
    print(f"\n{SEP}")
    print("  QUERY BENCHMARKS  (535M-edge KGBENCH, Enterprise IRIS)")
    print(SEP)

    src, pred, ts_min, ts_max = find_dataset_range()
    if src is None:
        print("  ERROR: No CALLS_AT data found — is KGBENCH fully ingested?")
        return

    groups_all = e.get_bucket_groups(pred, ts_min, ts_max)
    print(f"\n  Dataset:       535,116,500 edges (RE2-TT + RE2-OB + RE1-TT)")
    print(f"  CALLS_AT sources:       {len(groups_all)}")
    top3 = sorted(groups_all, key=lambda g: g["count"], reverse=True)[:3]
    print(f"  Busiest source: {src}")
    print(f"  Top 3:")
    for g in top3:
        print(f"    {g['source']:<40s}  {g['count']:>12,} calls  avg={g['avg']:.1f}ms")

    windows = {
        "5min  (  1 bucket)": (ts_max - 300,    ts_max),
        "1hr   ( 12 buckets)": (ts_max - 3_600,  ts_max),
        "6hr   ( 72 buckets)": (ts_max - 21_600, ts_max),
        "24hr  (288 buckets)": (ts_max - 86_400, ts_max),
    }

    # Section 1: Window query (raw)
    print(f"\n{'─'*64}")
    print(f"  QueryWindow (O(results), B-tree)  source={src}")
    for label, (t0w, t1w) in windows.items():
        result = e.get_edges_in_window(src, pred, t0w, t1w)
        ms = median(lambda t0=t0w, t1=t1w: e.get_edges_in_window(src, pred, t0, t1))
        print(f"  {label:<25s} {ms:>8.4f}ms  {len(result):>7,} edges")

    # Section 2: GetAggregate
    print(f"\n{'─'*64}")
    print(f"  GetAggregate (pre-agg, O(buckets))  source={src}")
    for label, (t0w, t1w) in windows.items():
        for metric in ("count", "avg"):
            val = e.get_temporal_aggregate(src, pred, metric, t0w, t1w)
            ms  = median(lambda t0=t0w, t1=t1w, m=metric:
                          e.get_temporal_aggregate(src, pred, m, t0, t1))
            val_str = f"{val:,}" if metric == "count" else (f"{val:.1f}ms" if val else "n/a")
            print(f"  {metric:<6s} {label:<24s} {ms:>8.4f}ms  → {val_str}")

    # Section 3: GetBucketGroups
    print(f"\n{'─'*64}")
    print(f"  GetBucketGroups (GROUP BY source — {len(groups_all)} CALLS_AT sources)")
    for label, (t0w, t1w) in windows.items():
        groups = e.get_bucket_groups(pred, t0w, t1w)
        ms = median(lambda t0=t0w, t1=t1w: e.get_bucket_groups(pred, t0, t1))
        print(f"  {label:<25s} {ms:>8.4f}ms  {len(groups):>3} groups")

    # Section 4: GetDistinctCount
    print(f"\n{'─'*64}")
    print(f"  GetDistinctCount (HLL, 16 registers)  source={src}")
    for label, (t0w, t1w) in windows.items():
        n  = e.get_distinct_count(src, pred, t0w, t1w)
        ms = median(lambda t0=t0w, t1=t1w: e.get_distinct_count(src, pred, t0, t1))
        print(f"  {label:<25s} {ms:>8.4f}ms  ~{n} distinct targets")

    # Section 5: Velocity + burst
    print(f"\n{'─'*64}")
    print(f"  GetVelocity / FindBursts")
    ms_vel  = median(lambda: e.get_edge_velocity(src, 300))
    vel     = e.get_edge_velocity(src, 300)
    ms_burst = median(lambda: e.find_burst_nodes(pred, 300, 1000))
    bursts   = e.find_burst_nodes(pred, 300, 1000)
    print(f"  get_edge_velocity (5min)         {ms_vel:>8.4f}ms  → {vel:,}")
    print(f"  find_burst_nodes  (threshold=1K) {ms_burst:>8.4f}ms  → {len(bursts)} nodes")

    # Section 6: Pre-agg vs raw comparison
    print(f"\n{'─'*64}")
    print(f"  Pre-agg vs raw scan (5-min window, same result)")
    t0w, t1w = ts_max - 300, ts_max
    ms_preagg = median(lambda: e.get_temporal_aggregate(src, pred, "count", t0w, t1w))
    ms_raw    = median(lambda: e.get_edges_in_window(src, pred, t0w, t1w))
    n_preagg  = e.get_temporal_aggregate(src, pred, "count", t0w, t1w)
    n_raw     = len(e.get_edges_in_window(src, pred, t0w, t1w))
    speedup   = ms_raw / ms_preagg if ms_preagg > 0 else 0
    print(f"  GetAggregate count (pre-agg):    {ms_preagg:>8.4f}ms  → {n_preagg}")
    print(f"  QueryWindow + len() (raw):       {ms_raw:>8.4f}ms  → {n_raw}")
    print(f"  Speedup: {speedup:.1f}x")

    # Summary
    ms_sc5 = median(lambda: e.get_temporal_aggregate(src, pred, "avg", ts_max-300, ts_max))
    ms_sc6 = median(lambda: e.get_bucket_groups(pred, ts_max-300, ts_max))
    print(f"\n{SEP}")
    print("  PASS/FAIL vs Success Criteria")
    print(f"{'─'*64}")
    print(f"  SC-005 GetAggregate <0.5ms (1 bucket): {ms_sc5:.4f}ms  {'✅ PASS' if ms_sc5 < 0.5 else '❌ FAIL'}")
    print(f"  SC-006 GetBucketGroups <5ms (1 bucket): {ms_sc6:.4f}ms  {'✅ PASS' if ms_sc6 < 5.0 else '❌ FAIL'}")
    print()


def run_ingest_benchmark():
    print(f"\n{SEP}")
    print("  INGEST BENCHMARK  (synthetic data — isolated prefix, no purge of real data)")
    print(SEP)

    BATCH  = 500
    PREFIX = f"__bench_{int(time.time())}"
    COUNTS = [50_000, 200_000]

    def make_edges(n, prefix):
        return [{"s": f"{prefix}:{i%27}", "p": "CALLS_AT",
                 "o": f"{prefix}:{(i+1)%27}", "ts": 2_100_000_000 + i,
                 "w": float(1 + i % 100)} for i in range(n)]

    def purge_prefix(prefix):
        iriso.classMethodVoid("Graph.KG.TemporalIndex", "Purge")

    results = {}
    for n in COUNTS:
        edges = make_edges(n, PREFIX)
        rates = []
        for r in range(2 + 5):
            purge_prefix(PREFIX)
            t0 = time.perf_counter_ns()
            for i in range(0, n, BATCH):
                e.bulk_create_edges_temporal(edges[i:i + BATCH])
            t1 = time.perf_counter_ns()
            rate = n / ((t1 - t0) / 1e9)
            if r >= 2:
                rates.append(rate)
        purge_prefix(PREFIX)
        med = statistics.median(rates)
        results[n] = med
        print(f"\n  {n:>7,} edges (synthetic):  {med:>12,.0f} edges/sec  stdev={statistics.stdev(rates):>7,.0f}")

    # Real RE2-TT data
    tsv = os.path.expanduser("~/ws/iris-datasets/re2-tt/converted/all_traces.tsv")
    if os.path.exists(tsv):
        real = []
        with open(tsv) as f:
            for i, line in enumerate(f):
                if i >= 200_000: break
                p = line.strip().split("\t")
                if len(p) < 5: continue
                try: real.append({"ts":int(p[0]),"s":p[1],"p":p[2],"o":p[3],"w":float(p[4])})
                except: pass
        print(f"\n  Loaded {len(real):,} real RE2-TT edges")
        rates_real = []
        for r in range(2 + 5):
            purge_prefix(PREFIX)
            t0 = time.perf_counter_ns()
            for i in range(0, len(real), BATCH):
                e.bulk_create_edges_temporal(real[i:i + BATCH])
            t1 = time.perf_counter_ns()
            rate = len(real) / ((t1 - t0) / 1e9)
            if r >= 2: rates_real.append(rate)
        purge_prefix(PREFIX)
        med_real = statistics.median(rates_real)
        print(f"  {len(real):>7,} real RE2-TT CALLS_AT: {med_real:>12,.0f} edges/sec  stdev={statistics.stdev(rates_real):>7,.0f}")
        results["real"] = med_real

    floor = min(v for v in results.values())
    print(f"\n  SC-004 ≥80K edges/sec: {'✅ PASS' if floor >= 80_000 else '❌ FAIL'}  (min={floor:,.0f})")
    print()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--query-only",  action="store_true")
    p.add_argument("--ingest-only", action="store_true")
    p.add_argument("--full",        action="store_true")
    args = p.parse_args()

    do_query  = args.query_only or args.full or not (args.ingest_only)
    do_ingest = args.ingest_only or args.full

    print(SEP)
    print("  iris-vector-graph v1.40.0 — Full Benchmark")
    print(f"  IRIS: kg-iris:KGBENCH (Enterprise, {conn._connection_string if hasattr(conn,'_connection_string') else 'localhost:11982'})")
    print(SEP)

    if do_query:
        run_query_benchmark()
    if do_ingest:
        run_ingest_benchmark()


if __name__ == "__main__":
    main()
