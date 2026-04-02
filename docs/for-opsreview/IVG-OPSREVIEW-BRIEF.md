# IVG for OpsReview — Capability Brief

*Written for: Chad Severtson, Erik Hemdal, Andre Cerri and the MONITOR OPUS team*
*Last updated: 2026-04-02*

---

## The One-Sentence Version

IVG turns IRIS into a temporal graph database — so instead of asking "what were the P-Buttons metrics at 14:07?", you can ask "which Ensemble jobs were calling which SQL queries, at what rate, and with what latency, in the 5 minutes before the WDQsz spike?"

---

## What OpsReview Can Do Today vs. With IVG

| Question | Today (P-Buttons) | With IVG |
|----------|------------------|----------|
| "When did the WD queue spike?" | ✅ Minute-level mgstat aggregates | ✅ Same, with sub-second resolution if collector is faster |
| "What caused the spike?" | ❌ P-Buttons is infrastructure only | ✅ Graph query: what was calling what at that timestamp |
| "Which process was responsible?" | ❌ No process-level data | ✅ If routine sampler feeds IVG: `CALLS_AT` edges with routine name |
| "Is this burst pattern recurring?" | ❌ Manual inspection | ✅ `find_burst_nodes()` over any time window, <1ms |
| "How does auth service latency trend over 24 hours?" | ❌ Not tracked per-service | ✅ `get_temporal_aggregate("svc:auth","CALLS_AT","avg",start,end)` — O(1) |
| "Which queries ran during that incident window?" | ❌ SQL Statement Index only has totals | ✅ Time-windowed query: `get_edges_in_window("sql:query_X","RAN_AT",ts1,ts2)` |
| "Which services are talking to which databases?" | ❌ Requires manual tracing | ✅ `MATCH (s:Service)-[:CALLS_AT]->(d:Database) RETURN s,d` |
| "How many distinct services hit auth in last hour?" | ❌ Not tracked | ✅ `get_distinct_count("svc:auth","CALLS_AT",now-3600,now)` |

---

## The Core Idea: P-Buttons + IVG

P-Buttons gives you **what the machine was doing** (CPU%, I/O, WD queue).
IVG gives you **what the application was doing** (which jobs, calling which routines, at what rate).

The combination answers Chad's core question:

> *"My system is slow and in 5 minutes I can figure out where the slowness is."*

---

## Five Concrete Roadmap Features

### 1. Routine Call Graph — "Who's burning the CPU right now?"

**The idea** (from Chad's Mar 3 meeting): a statistical profiler that samples the IRIS PID table several hundred times/second and records `(routine, tag, pid)`. Feed each sample as an `OBSERVED_AT` edge:

```
pid:1234 --OBSERVED_AT--> routine:^MGRLIB.SQLSRV+47  (ts=1712000042)
pid:1234 --OBSERVED_AT--> routine:^EnsLib.HTTP+12    (ts=1712000043)
```

**What you get with IVG:**

```python
# Top 10 hottest routines in the last 60 seconds
groups = engine.get_bucket_groups("OBSERVED_AT", ts_start=now-60, ts_end=now)
top = sorted(groups, key=lambda g: g["count"], reverse=True)[:10]
```

```
routine:^SQLSRV+47    count=2847  (34% of samples → 34% of CPU)
routine:^EnsHL7+8     count=1203  (14%)
routine:^ZEN.Page+22  count=891   (10%)
```

**Why it matters:** Today Chad can see "CPU is 87%" but not "which of my 175 Ensemble jobs is burning it." This feature closes that gap completely, using P-Buttons as the trigger and IVG as the time-series graph store.

**Implementation effort:** Medium. Sampler writes to IVG via `bulk_create_edges_temporal`. Works from embedded Python inside the IRIS being profiled, or from an external host via TCP.

---

### 2. Service Call Topology — "What's talking to what?"

If the IRIS instance being monitored has OpenTelemetry or Ensemble message tracing enabled, feed span data into IVG as `CALLS_AT` edges:

```
svc:HS.MPI --CALLS_AT--> svc:HS.Gateway  (ts, weight=latency_ms)
svc:HS.Gateway --CALLS_AT--> db:HSDB      (ts, weight=latency_ms)
```

**What you get:**

```python
# Average latency per service pair in last 5 minutes
groups = engine.get_bucket_groups("CALLS_AT", ts_start=now-300, ts_end=now)

# PPR from a slow service — what's upstream?
ops.kg_PPR_GUIDED_SUBGRAPH(seed_ids=["svc:HS.Gateway"], top_k=20)

# Cypher: find all services with avg latency > 100ms
engine.execute_cypher("""
    MATCH (a)-[r:CALLS_AT]->(b)
    WHERE r.avg_weight > 100
    RETURN a.id, b.id, r.avg_weight
    ORDER BY r.avg_weight DESC
""")
```

**Why it matters:** HealthShare customers have 175+ Ensemble jobs. The current advisory process involves reading ^Ens.MessageBody manually. IVG gives you a queryable call graph automatically from trace data.

**Implementation effort:** Low-Medium. Data comes from Ensemble message logs or OpenTelemetry. IVG ingests it.

---

### 3. Incident Correlation — "What else changed when the WD queue spiked?"

P-Buttons detects the anomaly (WDQsz z-score = 9.32 at 14:07:23). IVG answers "what was different in the 5 minutes before vs. the baseline?"

```python
incident_ts = 1712000843  # 14:07:23
baseline_start = incident_ts - 3600
baseline_end   = incident_ts - 300
incident_start = incident_ts - 300
incident_end   = incident_ts

# What's the call rate ratio between incident window and baseline?
for service in ["svc:HS.MPI", "svc:HS.Gateway", "svc:HL7.Engine"]:
    baseline = engine.get_temporal_aggregate(service, "CALLS_AT", "count", baseline_start, baseline_end)
    incident = engine.get_temporal_aggregate(service, "CALLS_AT", "count", incident_start, incident_end)
    if baseline > 0:
        ratio = incident / (baseline / 12)  # normalize to same window size
        if ratio > 2.0:
            print(f"{service}: {ratio:.1f}x surge during incident")
```

```
svc:HL7.Engine:  4.2x surge during incident   ← root cause candidate
svc:HS.MPI:      1.1x (normal)
```

**Why it matters:** This is the "5-minute root cause" Chad wants. P-Buttons spots the spike; IVG finds the process pattern that preceded it.

**Implementation effort:** Low. Uses existing `get_temporal_aggregate()` — just needs a data source feeding IVG.

---

### 4. SQL Query Timeline — "When did this slow query start running?"

Feed the IRIS SQL Statement Index into IVG as `EXECUTED_AT` edges:

```
process:12345 --EXECUTED_AT--> sql:query_hash_abc  (ts, weight=total_time_ms)
```

**What you get:**

```python
# How often did query X run in each hour over the last week?
hourly = []
for hour_offset in range(168):  # 7 days
    t0 = week_ago + hour_offset * 3600
    count = engine.get_temporal_aggregate("sql:query_hash_abc", "EXECUTED_AT", "count", t0, t0+3600)
    avg_ms = engine.get_temporal_aggregate("sql:query_hash_abc", "EXECUTED_AT", "avg", t0, t0+3600)
    hourly.append({"hour": hour_offset, "count": count, "avg_ms": avg_ms})

# Find when a query first appeared (regression detection)
first_seen = engine.get_edges_in_window("sql:query_hash_abc", "EXECUTED_AT", week_ago, now)
first_seen.sort(key=lambda e: e["ts"])
print("First seen:", datetime.fromtimestamp(first_seen[0]["ts"]))
```

**Why it matters:** The SQL Workload Intelligence spec (opsreview/specs/002) identifies query cost groups. IVG adds the time dimension — "this query group spiked 3x on Tuesday, matching the reported slowdown."

**Implementation effort:** Low. Feed from `%SYS.PTools` or Statement Index into IVG. This is a natural complement to spec 002.

---

### 5. Customer Health Knowledge Graph — "Which customers have this pattern?"

Store per-customer system telemetry as a knowledge graph. Each metric observation becomes a node + temporal edge:

```
customer:ManifestMedex --HAS_INSTANCE--> instance:MMEXEG01
instance:MMEXEG01 --EMITS_METRIC_AT--> metric:WDQsz  (ts, weight=163059)
instance:MMEXEG01 --EMITS_METRIC_AT--> metric:PhyWrs  (ts, weight=17428)
```

**What you get:**

```python
# Cypher: find all customers where WDQsz exceeded 10000 in the last 30 days
engine.execute_cypher("""
    MATCH (c:Customer)-[:HAS_INSTANCE]->(i:Instance)-[r:EMITS_METRIC_AT]->(m:Metric)
    WHERE m.id = 'metric:WDQsz'
      AND r.weight > 10000
    RETURN c.id, i.id, r.weight, r.ts
    ORDER BY r.weight DESC
""")

# PPR: given ManifestMedex has a WD issue, find structurally similar customers
similar = ops.kg_PPR_GUIDED_SUBGRAPH(seed_ids=["instance:MMEXEG01"], top_k=10)
# → other customers with similar metric signatures
```

```python
# Burst detection: which customers had unusual WD activity this week?
bursts = engine.find_burst_nodes(
    predicate="EMITS_METRIC_AT",
    window_seconds=604800,  # 7 days
    threshold=5,            # at least 5 spike events
)
# [{"id": "instance:MMEXEG01", "velocity": 23}, {"id": "instance:CUST007", "velocity": 11}]
```

**Why it matters:** OpsReview currently analyzes one customer at a time. IVG enables cross-customer pattern matching — "this specific combination of WD + I/O + CPU pattern has appeared before, at ManifestMedex in Nov 2025 and at HealthPartners in Dec 2025. Here's what resolved it."

**Implementation effort:** Medium-High. Requires ingesting historical P-Buttons data from `svcpbuttons2` into IVG. Andre owns that pipeline. The schema maps cleanly to temporal edges.

---

## Performance Numbers (Real Benchmarks)

All numbers from RE2-TT/RE2-OB/RE1-TT microservice trace datasets (535M edges total, Enterprise IRIS):

| Operation | Latency | Notes |
|-----------|---------|-------|
| Ingest | 134K edges/sec sustained | With pre-aggregation active |
| Window query | 0.1ms | O(results), not O(total edges) |
| GetAggregate (1 bucket = 5min window) | 0.085ms | Pre-computed at ingest time |
| GetAggregate (24-hour window) | 0.160ms | 288 buckets — still O(buckets) |
| GetBucketGroups (GROUP BY source) | 0.195ms | 3 services, 1 bucket |
| find_burst_nodes | 0.2ms | Threshold scan over bucket counters |
| Cypher MATCH (selective) | <5ms | Depends on graph structure |
| PageRank (10K nodes) | 62ms | Pure ObjectScript |

The key architectural property: **window queries are O(results), not O(total edges**. A 5-minute window query over 535M total edges takes 0.1ms because IRIS walks the B-tree directly to the matching timestamp range — it never scans the 534M+ edges outside the window.

---

## How to Feed Data Into IVG

### Option A: Embedded Python inside IRIS (zero infrastructure)

For a routine sampler or Ensemble trace collector running inside IRIS:

```objectscript
ClassMethod RecordCall(source As %String, target As %String, latencyMs As %Double) [ Language = python ]
{
    from iris_vector_graph.embedded import EmbeddedConnection
    from iris_vector_graph.engine import IRISGraphEngine
    import time
    engine = IRISGraphEngine(EmbeddedConnection())
    engine.create_edge_temporal(source, "CALLS_AT", target, int(time.time()), latencyMs)
}
```

No external Python process. No connection management. Runs inside the IRIS being monitored.

### Option B: External Python (existing OpsReview agent pattern)

```python
import iris
from iris_vector_graph.engine import IRISGraphEngine

conn = iris.connect(hostname='dpgenai1', port=1972, namespace='KGBENCH',
                    username='_SYSTEM', password='...')
engine = IRISGraphEngine(conn)

# Bulk-feed P-Buttons data as temporal edges
edges = [
    {"s": f"instance:{instance}", "p": "EMITS_METRIC_AT", "o": f"metric:{col}",
     "ts": int(row.ts.timestamp()), "w": float(row[col])}
    for col in ["WDQsz", "PhyWrs", "PhyRds", "Glorefs"]
    for _, row in df.iterrows()
    if not pd.isna(row[col])
]
engine.bulk_create_edges_temporal(edges)
```

This is exactly how `svcpbuttons2` data would flow into IVG — a daily or hourly job feeding metric observations as temporal edges.

---

## Suggested Roadmap Sequence

| Phase | Feature | Effort | Impact |
|-------|---------|--------|--------|
| **1** | Feed P-Buttons metrics into IVG as temporal edges | Low | Enables all time-series queries on existing data |
| **2** | Incident correlation (Feature 3) | Low | Immediate: "what spiked with the WD queue?" |
| **3** | SQL query timeline (Feature 4) | Low | Complements spec 002, adds time dimension |
| **4** | Routine sampler → IVG (Feature 1) | Medium | Closes the process-level observability gap Chad identified |
| **5** | Cross-customer health KG (Feature 5) | Medium-High | "Find me other customers with this pattern" |
| **6** | Service call topology from Ensemble (Feature 2) | Medium | Full call graph visibility inside HealthShare |

Phase 1 is a one-day integration task (modify Andre's ETL to write to IVG). Phases 2-3 are the immediate payoff — they require only Phase 1 data. Phase 4 is what Chad described in the Mar 3 meeting as the hardest missing capability; IVG makes it straightforward once the sampler exists.

---

## Questions / Contact

Tom Dyar — thomas.dyar@intersystems.com  
`pip install iris-vector-graph` — PyPI package, MIT license, works on any IRIS 2024.1+  
Source: `https://github.com/intersystems-community/iris-vector-graph`
