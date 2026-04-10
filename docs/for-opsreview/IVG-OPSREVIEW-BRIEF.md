# IVG × OpsReview Integration Brief

**Date**: 2026-03-30
**Status**: Strategic — for CRISP prioritization and spec sequencing
**Audience**: OpsReview team (Tom), IVG maintainer (Andre)

---

## The Problem Statement

OpsReview spec 002 (SQL Workload Intelligence) answers "what is expensive?" via cost share ranking and parameter sensitivity. But advisors immediately ask the follow-up: **"When did it get expensive? Did it coincide with anything else?"**

Today there is no time dimension. The agent knows:
- QueryGroup A is 27.7% of total SQL cost
- QueryGroup A is parameter-sensitive (35.7× tail/normal ratio)

It does not know:
- Whether that 27.7% appeared last Tuesday or has been stable for 6 months
- Whether it spiked at the same moment WD queue depth spiked in the P-Buttons data
- Whether Customer B had the same query group go critical last quarter

IVG's temporal graph is the missing primitive that answers all three questions.

---

## IVG Capabilities Relevant to OpsReview

| Primitive | How | Current Speed |
|-----------|-----|--------------|
| k-hop ego graph | `BFSFastJson`, `kg_SUBGRAPH` | 1–20ms |
| Node similarity (KNN) | `kg_KNN_VEC` (HNSW, 384-dim) | 1.7ms |
| Community detection | `WCCJson`, `CDLPJson` | batch |
| Pattern matching | Cypher `[*1..3]`, EXISTS | varies |
| VECTOR similarity | IRIS `VECTOR(DOUBLE, 384)` + HNSW | <10ms |
| Multi-vector retrieval | PLAIDSearch | 9ms |

**What's missing for OpsReview** (see roadmap below):
- Temporal edges (`^KG("tout", ts, s, p, o)`) — not yet in globals
- Per-workload KG ingest pipeline (Statement Index → graph edges)
- Incident correlation across the P-Buttons + SQL signal stream

---

## Five IVG Features for OpsReview

### Feature 1: Routine Sampler → CPU Flamegraph

**What it does**: Translate `%SYS.PTools` routine samples (already captured in P-Buttons) into a call graph stored as IVG edges. Each `CALLED_BY` edge gets a weight = execution count.

**IVG role**: Stores call graph, runs PPR from hot nodes to surface highest-centrality routines.

**OpsReview role**: Advisor sees "Routine X is the hot path — it's called by 4 of your top 5 SQL groups."

**Infrastructure needed**: P-Buttons ETL → `CALLS` edge ingest. `pbuttons_tools.py` already parses the data.

**Dependency**: Requires IVG `opsreview-iris` container wired to P-Buttons namespace.

---

### Feature 2: Ensemble Call Topology

**What it does**: Map Business Service → Business Process → Business Operation routing chains from Ensemble/Interoperability message journal into IVG as a directed graph. Edge weight = message count.

**IVG role**: Stores the topology, detects high-betweenness-centrality bottleneck components.

**OpsReview role**: "Your Order Filler has a fan-in of 47 — it's the single point where these 12 SQL queries converge."

**Infrastructure needed**: Ensemble message log ETL (new data source — not yet in OpsReview).

**Dependency**: Requires access to `Ens.MessageHeader` / `Ens.MessageBody` tables on the customer instance.

---

### Feature 3: Incident Correlation

**What it does**: When a new OpsReview analysis runs, query IVG for whether any known pattern (from the PatternLibrary or a temporal graph anomaly) correlates with current findings. Output: "3 similar incidents in IVG knowledge base; last occurred Customer B, 2026-01-12."

**IVG role**: Pattern library lookup via VECTOR_COSINE similarity (already implemented in spec 002), extended with temporal context: "when did similar patterns first appear at each customer?"

**OpsReview role**: Proactive: "We've seen this before — Customer B fixed it with index X."

**Infrastructure needed**: Extends existing PatternLibrary (IRIS OpsReview_PatternLibrary table). Needs `analysis_date` field already present.

**Dependency**: Builds directly on spec 002's PatternLibrary. No new infrastructure required beyond populating the library with more data.

---

### Feature 4: SQL Query Timeline

**What it does**: Feed `INFORMATION_SCHEMA.STATEMENT_DAILY_STATS` into IVG as temporal edges:
```
QueryGroup_A --[COST_ON]--> Date_2026-03-15 (weight=cost_share_pct)
QueryGroup_A --[EXECUTED_AT]--> Date_2026-03-15 (weight=avg_latency_ms)
```

Time-window queries then answer: "Show me the 7-day cost trajectory for this group" and "Did the cost spike correlate with a P-Buttons WD queue spike?"

**IVG role**: Stores temporal cost graph. Sliding window query `$Order(^KG("tout", ts_start))` runs sub-millisecond. IVG then does neighborhood queries: "what else changed on the same days QueryGroup_A spiked?"

**OpsReview role**: Advisor gets a timeline view in the report: cost stable → sudden spike on date X → correlates with 3 other signals.

**Infrastructure needed**: `STATEMENT_DAILY_STATS` is already fetched in spec 002. The ETL step is a 30-line wrapper that calls `TemporalIndex.InsertEdge()` per row, passing `StatDate` as the `ts` parameter.

**Dependency on IVG side**: None — `Graph.KG.TemporalIndex` is fully built with `InsertEdge()`, `BulkInsert()`, `QueryWindow()`, `GetAggregate()`. All temporal globals (`^KG("tout")`, `^KG("tin")`, bucket aggregates, HLL sketches) are written today when you go through `TemporalIndex.InsertEdge()` rather than the SQL/`rdf_edges` path. The entire integration is OpsReview-side work.

---

### Feature 5: Cross-Customer Health Knowledge Graph

**What it does**: Aggregate anonymized QueryGroup signatures, severity, and resolution outcomes across all OpsReview analyses into a shared knowledge graph on `dpgenai1`. Each QueryGroup is a node; edges connect groups that co-occur or have been resolved by the same remediation.

**IVG role**: Full KG substrate on `dpgenai1`. Cypher queries: "find all QueryGroups within 2 hops of this one that were resolved by adding an index."

**OpsReview role**: Long-term: AI-assisted remediation recommendations grounded in a real cross-customer graph.

**Infrastructure needed**: Significant. Requires data anonymization pipeline, secure cross-customer store, consent/governance framework, `dpgenai1` IVG deployment.

**Dependency**: Everything else comes first. This is a 6–12 month horizon.

---

## Implementation Sequence (CRISP-aligned)

| Priority | Feature | IVG Gap | OpsReview Gap | Weeks |
|----------|---------|---------|---------------|-------|
| **P1** | Feature 3: Incident Correlation | None — VECTOR already in IRIS | Populate PatternLibrary with real runs | 1–2 |
| **P2** | Feature 4: SQL Query Timeline | None — TemporalIndex fully built | ETL: STATEMENT_DAILY_STATS → TemporalIndex.InsertEdge() | 2–3 |
| **P3** | Feature 1: Routine Sampler | None — graph ingest exists | P-Buttons ETL for routine samples | 4–6 |
| **P4** | Feature 2: Ensemble Topology | None | New data source: Ens.MessageHeader | 6–9 |
| **P5** | Feature 5: Cross-Customer KG | dpgenai1 deployment | Anonymization + governance | 12+ |

---

## Why Feature 4 (SQL Query Timeline) Is the Highest-Impact Next IVG Spec

1. **Direct extension of spec 002** — the data is already in memory. `STATEMENT_DAILY_STATS` is fetched in every run. The ETL from DataFrame → IVG edges is ~30 lines.

2. **Zero new infrastructure on the OpsReview side** — `opsreview-iris` container already exists. sqlalchemy-iris already connected.

3. **Answers the most urgent advisor question** — after seeing "QueryGroup A = 27.7% of cost", the next question is always "was it always this high?" A timeline answers it without a follow-up analysis.

4. **Unlocks Feature 3 (Incident Correlation) from the time dimension** — knowing *when* a pattern first appeared turns a static similarity match into a historical incident log.

5. **No IVG changes required** — `Graph.KG.TemporalIndex` is already fully implemented with `InsertEdge()`, `BulkInsert()`, `QueryWindow()`, `GetAggregate()`, `GetVelocity()`, `FindBursts()`. The integration is 100% OpsReview-side Python.

---

## The Integration Work (OpsReview-side only)

`Graph.KG.TemporalIndex` is already built. To connect `STATEMENT_DAILY_STATS` to it:

```python
# In sql_workload_tools.py — new method on SQLWorkloadSession
def ingest_to_ivg(self, groups: list[QueryGroup], daily_df: pd.DataFrame, engine):
    """Feed daily stats into IVG as temporal edges via TemporalIndex.InsertEdge()."""
    import datetime
    for group in groups:
        for _, row in daily_df[daily_df["Hash"].isin(group.member_hashes)].iterrows():
            ts = int(datetime.datetime.strptime(str(row["StatDate"]), "%Y-%m-%d").timestamp())
            engine.callClassMethod(
                "Graph.KG.TemporalIndex", "InsertEdge",
                f"QueryGroup:{group.group_id}",   # source
                "COST_ON",                          # predicate
                f"Date:{row['StatDate']}",          # target
                ts,                                 # timestamp
                float(row["StatTotal"]),            # weight
            )
```

Then `QueryWindow()` and `GetAggregate()` answer "what was the cost trajectory?" without any new ObjectScript.

---

## CRISP Phase Mapping for Feature 4

| CRISP Phase | Output | Status |
|-------------|--------|--------|
| **C — Clarify** | Problem: "no time dimension on SQL cost data"; constraint: use existing IVG + IRIS | Done (this doc) |
| **R — Results** | Baseline: 0 temporal queries today; Target: advisor can ask "when did X spike?" in same report | Needs `success-metrics.md` |
| **I — Investigate** | Process flow: `STATEMENT_DAILY_STATS` → IVG edge ingest → temporal Cypher query → report section | Needs `process-flow.md` |
| **S — Spec** | AI Spec Sprint 1: temporal edges in IVG; ETL wrapper in `sql_workload_tools.py`; report section | Needs spec |
| **P — Prove** | Validate: report for a customer shows 7-day cost trajectory for top 3 groups | Deferred to post-build |
