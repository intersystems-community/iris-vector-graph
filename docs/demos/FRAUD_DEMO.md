<!-- markdownlint-disable MD013 -->

# Fraud Detection Demo

The fraud detection demo applies IVG's graph engine to real-time financial fraud detection. The AWS Neptune reference
notebook
[_Building a Fraud Graph Application on Amazon Neptune_](https://github.com/aws/graph-notebook/blob/main/src/graph_notebook/notebooks/01-Neptune-Database/03-Sample-Applications/01-Fraud-Graphs/01-Building-a-Fraud-Graph-Application.ipynb)
works through the same class of problem in Gremlin: fraud rings (first-party fraud) and identity theft (third-party
fraud) in credit card transaction data. The demo page scores single transactions; the Cypher further down shows how
those fraud-ring and identity patterns look on IVG.

For developers with a checkout of this repository: the demo server is in `src/iris_demo_server`, which the
`iris-vector-graph` wheel does not include. The fraud page itself reads nothing from IRIS; the patterns in
[Fraud patterns on IVG](#fraud-patterns-on-ivg) run on any namespace IVG is installed in.

## Running the Demo

```bash
pip install -e '.[demo]'
cd src
DEMO_MODE=true python -m uvicorn iris_demo_server.app:app --port 8200 --host 127.0.0.1
open http://localhost:8200/fraud
```

## What the page runs

Scores come from one of two places, and the page header says which:

- **Fraud API.** With `DEMO_MODE` unset, the page posts each transaction to the fraud
  scoring service at `FRAUD_API_URL` (default `http://localhost:8100`). That service is
  separate from this repository. When it requires a key (the bundled
  `src/iris_fraud_server` does with `IVG_API_KEY` set, on every route but its docs
  pages), set `FRAUD_API_KEY` to it: the page sends it as `X-Api-Key`.
- **Demo heuristic.** With `DEMO_MODE=true`, or when the API is unreachable and the
  circuit breaker opens, the score comes from the amount alone: above $10,000 scores
  0.85, above $5,000 0.65, above $2,000 0.35, anything else 0.15. Results are labelled
  "Demo Heuristic".

The transaction graph under each score is illustrative. Payer, device, merchant and IP
come from the submitted transaction; the historical transactions and related devices and
merchants are placeholders showing the shape of the graph, not rows read from IRIS. The
"IRIS SQL Graph Queries" and "Bitemporal Audit Queries" panels are reference queries; the
page does not run them.

### Four pre-built scenarios

Risk bands: low below 0.30, medium below 0.60, high below 0.85, critical from 0.85. The
levels below are what the demo heuristic returns.

| Scenario              | Amount     | Heuristic level | Intended signal                    |
| --------------------- | ---------- | --------------- | ---------------------------------- |
| Legitimate purchase   | $149.99    | LOW             | Trusted device, known merchant     |
| Suspicious activity   | $8,500.00  | HIGH            | New device, overseas merchant      |
| High-risk transaction | $25,000.00 | CRITICAL        | Tor browser, crypto exchange       |
| Late arrival          | $12,750.00 | CRITICAL        | Dormant account, offshore merchant |

The heuristic ignores device, merchant and IP; only a real scoring model uses them.

## Fraud patterns on IVG

The demo page does not run these; they show how the problems look on IVG, on a small graph whose answers are worked
by hand. Four accounts, two devices, and transfers that fan in to `acct:4` and then run down a chain:

```text
acct:1 -USES-> dev:A <-USES- acct:2        acct:3 -USES-> dev:B
acct:1, acct:2, acct:3 -TRANSFERS_TO-> acct:4 -TRANSFERS_TO-> acct:5 -> acct:6 -> acct:7
```

```python
import iris
from iris_vector_graph import IRISGraphEngine

conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "SYS")
engine = IRISGraphEngine(conn, embedding_dimension=768)
engine.initialize_schema()

for i in range(1, 8):
    engine.create_node(f"acct:{i}", labels=["Account"])
for d in ("dev:A", "dev:B"):
    engine.create_node(d, labels=["Device"])
for a, d in [("acct:1", "dev:A"), ("acct:2", "dev:A"), ("acct:3", "dev:B")]:
    engine.create_edge(a, "USES", d)
for s, t in [("acct:1", "acct:4"), ("acct:2", "acct:4"), ("acct:3", "acct:4"),
             ("acct:4", "acct:5"), ("acct:5", "acct:6"), ("acct:6", "acct:7")]:
    engine.create_edge(s, "TRANSFERS_TO", t)
```

**Rings: accounts that share a device.** First-party fraud pools identities; a shared device, email, phone or IP is
the link. `a.id < b.id` lists each pair once:

```python
engine.execute_cypher(
    "MATCH (a:Account)-[:USES]->(d:Device)<-[:USES]-(b:Account) WHERE a.id < b.id "
    "RETURN a.id, b.id, d.id"
).rows
# → [['acct:1', 'acct:2', 'dev:A']]
```

**Mules: accounts many others pay.** A mule gathers stolen funds before passing them on:

```python
engine.execute_cypher(
    "MATCH (source)-[:TRANSFERS_TO]->(hub) WITH hub, count(source) AS incoming "
    "WHERE incoming > 2 RETURN hub.id, incoming ORDER BY incoming DESC"
).rows
# → [['acct:4', 3]]
```

**Layering: where the money went, two to four hops on.** `acct:4` is one hop from `acct:1`, so it is not in the
answer:

```python
engine.execute_cypher(
    "MATCH p = (o {node_id: $acct})-[:TRANSFERS_TO*2..4]->(d) RETURN d.id, length(p) ORDER BY length(p)",
    {"acct": "acct:1"},
).rows
# → [['acct:5', 2], ['acct:6', 3], ['acct:7', 4]]
```

**Anomalies by vector similarity.** With an embedding per transaction (amount, merchant category, time of day,
device), a transaction far from its customer's usual ones scores low against them:

<!-- doc-test: skip reason="needs Transaction nodes with embeddings; the example graph has none" -->

```python
engine.execute_cypher(
    "CALL ivg.vector.search('Transaction', 'emb', $vector, 10) YIELD node, score "
    "WHERE score < 0.4 RETURN node.id, score",
    {"vector": current_transaction_vector},
)
```

### Bitemporal Audit Trail

A bitemporal design stores each risk score with two timestamps: when the transaction occurred (`valid_time`) and when
the score was computed (`system_time`). That supports time-travel queries for regulatory audit. The demo does not store
scores; these queries show the shape:

<!-- doc-test: skip reason="a design sketch: the demo stores no RiskScore nodes" -->

```cypher
-- Current risk score
MATCH (t:Transaction {id: $id})-[:HAS_SCORE]->(s:RiskScore)
RETURN s.score, s.computed_at

-- Score as of 30 days ago (what did we know then?)
MATCH (t:Transaction {id: $id})-[:HAS_SCORE]->(s:RiskScore)
WHERE s.system_time <= $thirty_days_ago
RETURN s.score ORDER BY s.system_time DESC LIMIT 1
```

## Why Graph vs. SQL

Traditional fraud detection uses flat tables with hand-crafted features. Graph traversal finds structural patterns
that flat models miss:

| Pattern                            | Flat model                                  | Graph                               |
| ---------------------------------- | ------------------------------------------- | ----------------------------------- |
| Shared device across accounts      | Requires self-join on account table         | 1-hop traversal from device node    |
| 4-hop money laundering chain       | A self-join per hop, one query per length   | Variable-length path `*1..4`        |
| Fraud ring (6 accounts, shared IP) | Requires separate graph computation offline | 2-hop traversal through the IP node |
| Velocity (5 txns in 10 min)        | Possible with window functions              | Same: temporal edges + time filter  |

In the AWS/Neptune case study
([Delivery Hero](https://aws.amazon.com/blogs/database/empowering-fraud-detection-at-delivery-hero-with-amazon-neptune/)),
graph traversal ran at 15ms against expensive MySQL JOINs and blocked 32% more fraudulent purchases. On IVG the same
patterns are Cypher queries on IRIS, and vector similarity search for anomalies runs in the same engine.

## Data model the patterns assume

```text
Account -[:USES]-> Device
Account -[:USES]-> IPAddress
Account -[:HAS_SCORE]-> RiskScore
Transaction -[:FROM]-> Account
Transaction -[:TO]-> Merchant
Transaction -[:USES]-> Device
Transaction -[:FROM_IP]-> IPAddress
Transaction -[:HAS_SCORE]-> RiskScore
```

## Architecture

```text
Browser (HTMX + D3.js)
    ↓ POST /api/fraud/score
FastHTML route (src/iris_demo_server/routes/fraud.py)
    ↓ FraudAPIClient (src/iris_demo_server/services/fraud_client.py)
Fraud API at FRAUD_API_URL, or the demo heuristic (DEMO_MODE=true / circuit open)
```
