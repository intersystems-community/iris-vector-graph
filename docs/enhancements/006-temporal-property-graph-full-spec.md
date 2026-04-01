# Temporal Property Graph Ingest and Query Spec for `iris-vector-graph`

## Status
Draft for initial implementation in `iris-vector-graph`

## Purpose
Implement temporal property-graph ingest and query in `iris-vector-graph`, using the existing `^KG` model and Cypher layer as the primary runtime abstraction.

The package is positioned as an IRIS-based graph/vector/text engine with SQL, openCypher, and GraphQL over a unified graph substrate, so temporal edges should extend that substrate rather than create a parallel one.

Reference:
- https://pypi.org/project/iris-vector-graph/

---

## 1. Scope

This spec covers:

1. Dataset targets for first-wave implementation
2. Canonical interchange schema
3. Conversion rules from observability datasets into the canonical graph model
4. `^KG` storage and temporal indexing extensions
5. Python and ObjectScript APIs
6. Cypher query behavior and procedure surface
7. Import/export formats
8. Acceptance criteria and implementation order

This spec does not cover:

- Full temporal Cypher language redesign
- TTL / expiry policies
- Streaming Kafka / Pulsar pipelines
- Long-term retention compaction
- Full RDF-first ingest for observability workloads

---

## 2. Datasets to Support First

### 2.1 Tier A: Immediate Fit

#### 2.1.1 RCAEval benchmark
- Zenodo: https://zenodo.org/records/14590730
- GitHub: https://github.com/phamquiluan/RCAEval

Why:
- Explicit metrics, logs, and traces RCA benchmark for microservices
- Suitable for service-call graph, incident graph, and temporal path queries
- Good primary target for validating root-cause-oriented temporal graph patterns

#### 2.1.2 Train-Ticket anomaly dataset
- Zenodo: https://zenodo.org/records/6979726
- Train-Ticket system: https://github.com/FudanSELab/train-ticket/

Why:
- Includes logs, Jaeger traces, and Prometheus KPI data
- Smaller and easier first ingest target than RCAEval full benchmark family
- Good first integration target for temporal service-call and KPI graphs

#### 2.1.3 TraceRCA
- GitHub: https://github.com/NetManAIOps/TraceRCA

Why:
- Trace-centric RCA workflow
- Clean fit for span graph and service-call temporal graph derivation

### 2.2 Tier B: Scale / Production Realism

#### 2.2.1 Alibaba cluster-trace-microservices-v2021
- Root repo: https://github.com/alibaba/clusterdata
- README: https://github.com/alibaba/clusterdata/blob/master/cluster-trace-microservices-v2021/README.md

Why:
- Production-scale microservice trace/runtime dataset
- Best scale validation for `CALLS_AT` edges and time-window scans

#### 2.2.2 Microsoft Cloud Monitoring Dataset
- GitHub: https://github.com/Microsoft/cloud-monitoring-dataset
- README: https://github.com/Microsoft/cloud-monitoring-dataset/blob/master/README.md

Why:
- KPI anomaly corpus
- Good validation set for metric-oriented temporal edges and burst windows

#### 2.2.3 IBM cloud anomaly dataset
- Zenodo: https://zenodo.org/records/14062900
- Paper / description: https://arxiv.org/pdf/2411.09047

Why:
- Extremely wide telemetry table
- Good for validating metric-heavy and bucket-heavy workloads

### 2.3 Tier C: Optional Workload Traces

#### 2.3.1 Azure Public Dataset
- GitHub: https://github.com/Azure/AzurePublicDataset
- README: https://github.com/Azure/AzurePublicDataset/blob/master/README.md

Why:
- Useful workload traces and infra traces
- Secondary target after service-centric observability ingestion is stable

### 2.4 Useful Derived Standard

#### 2.4.1 OpenTelemetry service graph connector
- README: https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/connector/servicegraphconnector/README.md

Why:
- Good reference model for deriving service-to-service graphs from traces
- Useful as the semantic baseline for OTel / Jaeger trace adapters

---

## 3. Canonical Graph Model

The canonical internal model is a temporal property graph.

- Nodes have stable string IDs
- Nodes may have one or more labels
- Relationships have a single type / predicate
- Temporal relationships carry a required integer Unix timestamp
- Rich attributes are stored as relationship properties

### 3.1 Canonical NDJSON Event Schema

This is the first interchange format to implement.

### 3.1.1 Temporal edge event

```json
{
  "kind": "temporal_edge",
  "source": "service:checkout",
  "predicate": "CALLS_AT",
  "target": "service:payment",
  "timestamp": 1712000000,
  "weight": 1.0,
  "source_labels": ["Service"],
  "target_labels": ["Service"],
  "attrs": {
    "dataset": "RCAEval_RE2_TT",
    "case_id": "tt_case_001",
    "trace_id": "abc123",
    "span_id": "def456",
    "latency_ms": 237,
    "status_code": 500,
    "error": true
  }
}
```

### 3.1.2 Node event

```json
{
  "kind": "node",
  "id": "service:checkout",
  "labels": ["Service"],
  "properties": {
    "name": "checkout",
    "namespace": "train-ticket"
  }
}
```

### 3.1.3 Non-temporal edge event

```json
{
  "kind": "edge",
  "source": "span:def456",
  "predicate": "BELONGS_TO",
  "target": "trace:abc123",
  "weight": 1.0,
  "attrs": {}
}
```

---

## 4. Canonical Entity and Edge Types

### 4.1 Node Labels

- `Service`
- `Host`
- `Pod`
- `Trace`
- `Span`
- `Metric`
- `MetricSample`
- `LogEvent`
- `Incident`

### 4.2 Relationship Types

- `CALLS_AT`
- `CHILD_OF_AT`
- `EMITS_METRIC_AT`
- `OBSERVED_AT`
- `IMPACTS_AT`
- `HOSTS_AT`
- `BELONGS_TO`
- `HAS_ROOT_CAUSE`

---

## 5. Conversion Rules by Source Type

### 5.1 OpenTelemetry / Jaeger Traces

Use the OTel service graph model as the reference for service-call derivation.

Reference:
- https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/connector/servicegraphconnector/README.md

#### 5.1.1 Service call graph

For a client/server or producer/consumer interaction:

- upsert `Service(caller)`
- upsert `Service(callee)`
- emit:

```json
{
  "kind": "temporal_edge",
  "source": "service:<caller>",
  "predicate": "CALLS_AT",
  "target": "service:<callee>",
  "timestamp": "<span_start_epoch_s>",
  "weight": 1.0,
  "attrs": {
    "trace_id": "<trace_id>",
    "span_id": "<span_id>",
    "latency_ms": "<duration_ms>",
    "status_code": "<status_code>",
    "error": "<bool>"
  }
}
```

#### 5.1.2 Span lineage

- `Span(parent) -[CHILD_OF_AT]-> Span(child)` at child start time
- `Span -[BELONGS_TO]-> Trace`

### 5.2 Metrics / KPI Time Series

For each metric point:

- create or upsert source node, usually `Service` or `Host`
- either:
  - emit `EMITS_METRIC_AT` to a `MetricSample` node, or
  - keep metric details in edge attrs only

Recommended first pass:

```json
{
  "kind": "temporal_edge",
  "source": "service:checkout",
  "predicate": "EMITS_METRIC_AT",
  "target": "metric:latency_ms",
  "timestamp": 1712000000,
  "weight": 237.0,
  "attrs": {
    "metric_name": "latency_ms",
    "aggregation": "avg",
    "dataset": "MicrosoftCloudMonitoring"
  }
}
```

### 5.3 Logs

For each log event:

```json
{
  "kind": "temporal_edge",
  "source": "log:<event_id>",
  "predicate": "OBSERVED_AT",
  "target": "service:<service_name>",
  "timestamp": 1712000000,
  "weight": 1.0,
  "attrs": {
    "level": "ERROR",
    "template": "...",
    "trace_id": "abc123"
  }
}
```

### 5.4 Incident / Fault Labels

For a labeled RCA case:

```json
{
  "kind": "temporal_edge",
  "source": "incident:<case_id>",
  "predicate": "IMPACTS_AT",
  "target": "service:<service_name>",
  "timestamp": 1712000000,
  "weight": 1.0,
  "attrs": {
    "fault_type": "resource_exhaustion",
    "dataset": "RCAEval_RE2_TT"
  }
}
```

Optionally:

```json
{
  "kind": "edge",
  "source": "incident:<case_id>",
  "predicate": "HAS_ROOT_CAUSE",
  "target": "service:<root_service>",
  "weight": 1.0,
  "attrs": {}
}
```

---

## 6. Storage Model in `^KG`

Extend the existing `^KG("out"/"in"/"deg"/"label"/"prop")` pattern with temporal subscripts.

### 6.1 Required Globals

```objectscript
^KG("out", source, predicate, target) = weight
^KG("in", target, predicate, source) = weight

^KG("tout", ts, source, predicate, target) = weight
^KG("tin", ts, target, predicate, source) = weight

^KG("bucket", bucket_id, source) = ""
^KG("bucketCount", bucket_id, source) = count

^KG("label", label, node) = ""
^KG("prop", node, key) = value
^KG("edgeprop", ts, source, predicate, target, key) = value
```

### 6.2 Notes

- `bucket_id = floor(ts / bucket_size_seconds)`
- rich attributes belong in `edgeprop`, not in `tout` or `tin`
- `bucketCount` is strongly recommended because it makes velocity queries O(1) per bucket rather than requiring bucket membership scans

---

## 7. Import / Export Formats

### 7.1 NDJSON Import / Export

Primary operational format.

#### 7.1.1 Python import API

```python
IRISGraphEngine.import_graph_ndjson(path, upsert_nodes=True, batch_size=10000)
IRISGraphEngine.import_temporal_edges_ndjson(path, batch_size=10000)
```

#### 7.1.2 Python export API

```python
IRISGraphEngine.export_graph_ndjson(path)
IRISGraphEngine.export_temporal_edges_ndjson(path, start=None, end=None, predicate=None)
```

### 7.2 CSV Node / Edge Import / Export

Best compatibility with property-graph tooling and Neo4j-style bulk loaders.

Reference:
- https://neo4j.com/docs/operations-manual/current/import/

#### 7.2.1 Nodes CSV

```csv
id:ID,labels:LABEL,name,namespace
service:checkout,Service,checkout,train-ticket
```

#### 7.2.2 Edges CSV

```csv
:START_ID,:END_ID,:TYPE,ts:long,weight:double,latency_ms:long,status_code:long,error:boolean
service:checkout,service:payment,CALLS_AT,1712000000,1.0,237,500,true
```

#### 7.2.3 Import API

```python
IRISGraphEngine.import_graph_csv(nodes_path, edges_path, delimiter=",")
IRISGraphEngine.import_temporal_edges_csv(edges_path, delimiter=",")
```

### 7.3 GraphML Export

Implement export first, import later.

Reference:
- https://tinkerpop.apache.org/docs/current/reference/

#### 7.3.1 Export API

```python
IRISGraphEngine.export_graphml(path, include_temporal_edges=True, start=None, end=None)
```

#### 7.3.2 GraphML mapping

- node labels become a `labels` attribute, pipe-joined if multiple
- edge predicate becomes `type`
- `ts` stored as an edge attribute
- edge attrs flattened as GraphML key/value attributes

---

## 8. Python API Spec

### 8.1 Core temporal operations

```python
def create_edge_temporal(
    source: str,
    predicate: str,
    target: str,
    timestamp: int | None = None,
    weight: float = 1.0,
    attrs: dict | None = None,
    source_labels: list[str] | None = None,
    target_labels: list[str] | None = None,
) -> None:
    ...
```

```python
def bulk_create_edges_temporal(
    batch: list[dict],
    batch_size: int = 10000,
) -> dict:
    ...
```

```python
def get_edges_in_window(
    source: str | None = None,
    target: str | None = None,
    predicate: str | None = None,
    start: int | None = None,
    end: int | None = None,
    direction: str = "out",
) -> list[dict]:
    ...
```

```python
def get_edge_velocity(
    node_id: str,
    window_seconds: int = 60,
    predicate: str | None = None,
    now: int | None = None,
) -> int:
    ...
```

```python
def find_burst_nodes(
    label: str | None = None,
    predicate: str | None = None,
    window_seconds: int = 60,
    threshold: int = 50,
    now: int | None = None,
) -> list[dict]:
    ...
```

### 8.2 Import / export

```python
def import_graph_ndjson(path: str, upsert_nodes: bool = True, batch_size: int = 10000) -> dict:
    ...

def import_temporal_edges_ndjson(path: str, batch_size: int = 10000) -> dict:
    ...

def export_graph_ndjson(path: str) -> dict:
    ...

def export_temporal_edges_ndjson(
    path: str,
    start: int | None = None,
    end: int | None = None,
    predicate: str | None = None,
) -> dict:
    ...

def import_graph_csv(nodes_path: str, edges_path: str, delimiter: str = ",") -> dict:
    ...

def import_temporal_edges_csv(edges_path: str, delimiter: str = ",") -> dict:
    ...

def export_graphml(
    path: str,
    include_temporal_edges: bool = True,
    start: int | None = None,
    end: int | None = None,
) -> dict:
    ...
```

---

## 9. ObjectScript API Spec

```objectscript
Class Graph.KG.TemporalIndex Extends %RegisteredObject
{

ClassMethod CreateEdge(
    source As %String,
    predicate As %String,
    target As %String,
    timestamp As %BigInt = "",
    weight As %Double = 1.0,
    attrs As %DynamicObject = ""
) As %Status

ClassMethod BulkInsert(batchJSON As %String) As %Integer

ClassMethod GetEdgesInWindow(
    source As %String = "",
    target As %String = "",
    predicate As %String = "",
    startTS As %BigInt = "",
    endTS As %BigInt = "",
    direction As %String = "out"
) As %DynamicArray

ClassMethod GetEdgeVelocity(
    nodeId As %String,
    windowSeconds As %Integer = 60,
    predicate As %String = "",
    nowTS As %BigInt = ""
) As %Integer

ClassMethod FindBurstNodes(
    label As %String = "",
    predicate As %String = "",
    windowSeconds As %Integer = 60,
    threshold As %Integer = 50,
    nowTS As %BigInt = ""
) As %DynamicArray

ClassMethod PurgeTemporalIndex() As %Status
}
```

---

## 10. Cypher Behavior Spec

### 10.1 First supported pattern: edge property filtering

Start with normal edge properties.

```cypher
MATCH (a:Service)-[r:CALLS_AT]->(b:Service)
WHERE r.ts >= $start AND r.ts < $end
RETURN a, b, r
ORDER BY r.ts ASC
LIMIT 100
```

### 10.2 Required semantics

- `r.ts` maps to the temporal edge timestamp
- results come from `^KG("tout")` when `r.ts` constraints are present
- if only topology is requested and no temporal predicate exists, existing `^KG("out")` behavior remains valid
- `r.latency_ms`, `r.status_code`, and similar fields resolve from `^KG("edgeprop", ...)`

### 10.3 Query classes to support

#### 10.3.1 Windowed service calls

```cypher
MATCH (a:Service)-[r:CALLS_AT]->(b:Service)
WHERE a.name = $service
  AND r.ts >= $start
  AND r.ts < $end
RETURN a.name AS source, b.name AS target, r.ts AS ts, r.latency_ms AS latency_ms
ORDER BY r.ts ASC
```

#### 10.3.2 Inbound calls in a window

```cypher
MATCH (a:Service)-[r:CALLS_AT]->(b:Service)
WHERE b.name = $service
  AND r.ts >= $start
  AND r.ts < $end
RETURN a.name AS caller, b.name AS callee, r.ts AS ts, r.status_code AS status_code
ORDER BY r.ts ASC
```

#### 10.3.3 Error propagation

```cypher
MATCH (a:Service)-[r:CALLS_AT]->(b:Service)
WHERE r.ts >= $start
  AND r.ts < $end
  AND r.error = true
RETURN a.name, b.name, r.ts, r.status_code
ORDER BY r.ts ASC
```

#### 10.3.4 Incident-local service neighborhood

```cypher
MATCH (i:Incident)-[x:IMPACTS_AT]->(s:Service)-[r:CALLS_AT]->(t:Service)
WHERE i.id = $case_id
  AND r.ts >= $start
  AND r.ts < $end
RETURN i.id, s.name, t.name, r.ts
ORDER BY r.ts ASC
```

### 10.4 Phase-2 path queries

#### 10.4.1 Bounded temporal path

```cypher
MATCH p = (a:Service)-[:CALLS_AT*1..3]->(b:Service)
WHERE a.name = $source
  AND b.name = $target
  AND ALL(rel IN relationships(p) WHERE rel.ts >= $start AND rel.ts < $end)
RETURN p
LIMIT 20
```

#### 10.4.2 Monotone-time path

```cypher
MATCH p = (a:Service)-[:CALLS_AT*1..4]->(b:Service)
WHERE a.name = $source
  AND b.name = $target
  AND ALL(rel IN relationships(p) WHERE rel.ts >= $start AND rel.ts < $end)
  AND ALL(i IN range(0, size(relationships(p)) - 2)
      WHERE relationships(p)[i].ts <= relationships(p)[i+1].ts)
RETURN p
LIMIT 20
```

#### 10.4.3 Max-gap path

```cypher
MATCH p = (a:Service)-[:CALLS_AT*1..4]->(b:Service)
WHERE a.name = $source
  AND b.name = $target
  AND ALL(i IN range(0, size(relationships(p)) - 2)
      WHERE relationships(p)[i+1].ts - relationships(p)[i].ts <= 60)
RETURN p
LIMIT 20
```

---

## 11. Procedure API for Early Delivery

Add procedures before extending full Cypher syntax deeply.

```cypher
CALL ivg.temporal.window($source, $predicate, $start, $end)
YIELD source, target, ts, weight, attrs
RETURN source, target, ts, attrs
ORDER BY ts
```

```cypher
CALL ivg.temporal.velocity($node_id, 60, $predicate)
YIELD node, velocity
RETURN node, velocity
```

```cypher
CALL ivg.temporal.bursts("Service", "CALLS_AT", 60, 50)
YIELD node, velocity
RETURN node, velocity
ORDER BY velocity DESC
```

```cypher
CALL ivg.temporal.inbound($target, "CALLS_AT", $start, $end)
YIELD source, target, ts, attrs
RETURN source, target, ts, attrs
ORDER BY ts
```

---

## 12. Dataset-Specific Loader Mappings

### 12.1 RCAEval / Train-Ticket / TraceRCA

- traces → `CALLS_AT`, `CHILD_OF_AT`, `BELONGS_TO`
- metrics → `EMITS_METRIC_AT`
- logs → `OBSERVED_AT`
- fault labels → `IMPACTS_AT`, optional `HAS_ROOT_CAUSE`

### 12.2 Alibaba microservice traces

- service dependency / call runtime records → `CALLS_AT`
- use attrs for response time, call rate, and errors

### 12.3 Microsoft Cloud Monitoring / IBM

- source node is service or host
- target is metric id
- emit `EMITS_METRIC_AT`
- weight carries scalar value

---

## 13. Example Files

### 13.1 Example NDJSON

```json
{"kind":"node","id":"service:checkout","labels":["Service"],"properties":{"name":"checkout"}}
{"kind":"node","id":"service:payment","labels":["Service"],"properties":{"name":"payment"}}
{"kind":"temporal_edge","source":"service:checkout","predicate":"CALLS_AT","target":"service:payment","timestamp":1712000000,"weight":1.0,"source_labels":["Service"],"target_labels":["Service"],"attrs":{"latency_ms":237,"status_code":500,"error":true}}
```

### 13.2 Example nodes CSV

```csv
id:ID,labels:LABEL,name
service:checkout,Service,checkout
service:payment,Service,payment
```

### 13.3 Example edges CSV

```csv
:START_ID,:END_ID,:TYPE,ts:long,weight:double,latency_ms:long,status_code:long,error:boolean
service:checkout,service:payment,CALLS_AT,1712000000,1.0,237,500,true
```

---

## 14. Test Matrix

### 14.1 Ingest correctness

- create one temporal edge and verify presence in `out`, `in`, `tout`, `tin`
- create same `(source, predicate, target)` at different timestamps and verify both timestamps exist
- create same `(source, predicate, target, timestamp)` twice and verify second write is idempotent
- verify node labels and properties are upserted correctly
- verify edge attrs land in `edgeprop`

### 14.2 Window queries

- get all outbound calls from one service in a 5-minute window
- get all inbound calls to one service in a 5-minute window
- empty-window query returns empty result with no error

### 14.3 Velocity / burst queries

- exact bucket count for one node over one window
- threshold burst query returns only expected nodes
- velocity with predicate filter behaves correctly

### 14.4 Cypher queries

- edge property filter on `r.ts`
- edge property projection from `edgeprop`
- incident-local neighborhood query
- procedure calls for `window`, `velocity`, and `bursts`

### 14.5 Export

- NDJSON round-trip preserves node ids, labels, edge types, timestamps, and attrs
- CSV export/import round-trip preserves graph structure
- GraphML export contains edge timestamps and flattened attrs

---

## 15. Acceptance Criteria

### 15.1 Schema and ingest

- canonical NDJSON schema implemented
- CSV nodes/edges format implemented
- per-dataset adapters for RCAEval and Train-Ticket implemented first
- `create_edge_temporal()` writes to `out`, `in`, `tout`, `tin`, `bucket`, and `bucketCount`

### 15.2 Query

- Cypher edge-property filtering on `r.ts` works
- procedure APIs for `window`, `velocity`, and `bursts` work
- phase-2 bounded temporal path support works

### 15.3 Performance

- `bulk_create_edges_temporal(100K)` under 2 seconds on standard IRIS hardware
- 1-minute window query on million-edge graph under 10 ms
- burst detection on bucket counts under 100 ms

---

## 16. Recommended Implementation Order

1. canonical NDJSON schema
2. `Graph.KG.TemporalIndex` plus Python wrappers
3. RCAEval and Train-Ticket adapters
4. Cypher `r.ts` filtering
5. temporal procedures
6. CSV import/export
7. GraphML export
8. Alibaba-scale validation
9. phase-2 temporal path semantics

---

## 17. Summary

The implementation strategy is:

- use `iris-vector-graph` first
- extend `^KG` rather than creating a parallel temporal store
- make NDJSON the canonical operational interchange format
- support CSV for bulk graph exchange
- support GraphML export for tool interoperability
- keep Cypher as the primary query language
- layer temporal procedures first, then deeper temporal path semantics

