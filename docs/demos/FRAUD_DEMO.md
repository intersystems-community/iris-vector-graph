# Fraud Detection Demo

The fraud detection demo shows how IVG's graph engine enables real-time financial fraud detection — the same class of problem described in the AWS Neptune reference notebook [_Building a Fraud Graph Application on Amazon Neptune_](https://github.com/aws/graph-notebook/blob/main/src/graph_notebook/notebooks/01-Neptune-Database/03-Sample-Applications/01-Fraud-Graphs/01-Building-a-Fraud-Graph-Application.ipynb), which covers fraud rings (first-party fraud) and identity theft (third-party fraud) in credit card transaction data using Gremlin. The demo page scores single transactions; the Cypher further down shows how those fraud-ring and identity patterns look on IVG.

## Running the Demo

```bash
cd src
DEMO_MODE=true python -m uvicorn iris_demo_server.app:app --port 8200 --host 127.0.0.1
open http://localhost:8200/fraud
```

## What the page runs

Scores come from one of two places, and the page header says which:

- **Fraud API.** With `DEMO_MODE` unset, the page posts each transaction to the fraud
  scoring service at `FRAUD_API_URL` (default `http://localhost:8100`). That service is
  separate from this repository.
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

The Cypher below shows how the same problems look on IVG. It is reference material; the
demo page does not run it.

**Ring detection** — Find clusters of accounts sharing identifiers (email, phone, device, IP address). Classic first-party fraud: a group of people pool false identities to max out credit, then default.

```cypher
MATCH (a:Account)-[:USES]->(d:Device)<-[:USES]-(b:Account)
WHERE a <> b
RETURN a.id, b.id, d.id LIMIT 10
```

**Money mule / hub-and-spoke** — Detect accounts with abnormally high in-degree receiving from many sources. Mule accounts aggregate stolen funds before forwarding to a controller.

```cypher
MATCH (source)-[:TRANSFERS_TO]->(hub)
WITH hub, count(source) AS incoming
WHERE incoming > 5
RETURN hub.id, incoming ORDER BY incoming DESC
```

**Multi-hop transaction paths** — Trace funds through intermediary accounts up to N hops. Layering schemes deliberately obscure the money trail; graph traversal reconstructs it.

```cypher
MATCH p = (origin)-[:TRANSFERS_TO*2..4]->(destination)
WHERE origin.id = $account_id
RETURN p LIMIT 20
```

**Vector anomaly detection** — Flag transactions whose embedding is far from a customer's historical pattern. Embedding encodes amount, merchant category, time-of-day, and device.

```cypher
CALL ivg.vector.search('Transaction', 'emb', $current_txn_vector, 10)
YIELD node, score
WHERE score < 0.4
RETURN node.id, score
```

### Bitemporal Audit Trail

A bitemporal design stores each risk score with two timestamps: when the transaction occurred (`valid_time`) and when the score was computed (`system_time`). That supports time-travel queries for regulatory audit. The demo does not store scores; these queries show the shape:

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

Traditional fraud detection uses flat tables with hand-crafted features. Graph traversal finds **structural patterns** that flat models miss:

| Pattern                            | Flat model                                  | Graph                               |
| ---------------------------------- | ------------------------------------------- | ----------------------------------- |
| Shared device across accounts      | Requires self-join on account table         | 1-hop traversal from device node    |
| 4-hop money laundering chain       | 4 nested JOINs, exponential cost            | Variable-length path `*1..4`        |
| Fraud ring (6 accounts, shared IP) | Requires separate graph computation offline | Real-time community detection       |
| Velocity (5 txns in 10 min)        | Possible with window functions              | Same — temporal edges + time filter |

The AWS/Neptune case study ([Delivery Hero](https://aws.amazon.com/blogs/database/empowering-fraud-detection-at-delivery-hero-with-amazon-neptune/)) found graph traversal ran at **15ms** vs. expensive MySQL JOINs and blocked **32% more fraudulent purchases**. IVG achieves the same patterns with Cypher on IRIS, with the added benefit of vector similarity search for anomaly detection in the same engine.

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
