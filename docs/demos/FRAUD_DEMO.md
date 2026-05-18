# Fraud Detection Demo

The fraud detection demo shows how IVG's graph engine enables real-time financial fraud detection — the same class of problem AWS published about using Amazon Neptune ([Delivery Hero case study](https://aws.amazon.com/blogs/database/empowering-fraud-detection-at-delivery-hero-with-amazon-neptune/), [fraud graph application notebook](https://github.com/aws/graph-notebook/blob/main/src/graph_notebook/notebooks/01-Neptune-Database/03-Sample-Applications/01-Fraud-Graphs/01-Building-a-Fraud-Graph-Application.ipynb)), but running entirely on IRIS with Cypher instead of Gremlin.

## Running the Demo

```bash
# Start IRIS
docker compose up -d

# Install dependencies
pip install "iris-vector-graph[full]"

# Start demo server
python -m uvicorn iris_demo_server.app:app --port 8200 --host 127.0.0.1 --app-dir src

# Open browser
open http://localhost:8200/fraud
```

## What It Demonstrates

### Four Pre-Built Scenarios

| Scenario | Amount | Risk Level | Key Signal |
|----------|--------|-----------|------------|
| Legitimate purchase | $149.99 | LOW | Trusted device, known merchant |
| Suspicious activity | $8,500 | HIGH | New merchant + foreign IP |
| High-risk transaction | $25,000 | CRITICAL | Tor browser + crypto exchange |
| Late arrival | ~$200 | MEDIUM | 72-hour settlement delay |

### Fraud Detection Patterns

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

Every risk score is stored with two timestamps: when the transaction occurred (`valid_time`) and when the score was computed (`system_time`). This enables time-travel queries for regulatory audit:

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

| Pattern | Flat model | Graph |
|---------|-----------|-------|
| Shared device across accounts | Requires self-join on account table | 1-hop traversal from device node |
| 4-hop money laundering chain | 4 nested JOINs, exponential cost | Variable-length path `*1..4` |
| Fraud ring (6 accounts, shared IP) | Requires separate graph computation offline | Real-time community detection |
| Velocity (5 txns in 10 min) | Possible with window functions | Same — temporal edges + time filter |

The AWS/Neptune case study ([Delivery Hero](https://aws.amazon.com/blogs/database/empowering-fraud-detection-at-delivery-hero-with-amazon-neptune/)) found graph traversal ran at **15ms** vs. expensive MySQL JOINs and blocked **32% more fraudulent purchases**. IVG achieves the same patterns with Cypher on IRIS, with the added benefit of vector similarity search for anomaly detection in the same engine.

## Data Model

```
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

```
Browser (HTMX + D3.js)
    ↓ POST /api/fraud/score
FastHTML route (src/iris_demo_server/routes/fraud.py)
    ↓ engine.execute_cypher()
IRISGraphEngine + IRISGraphStore
    ↓ SQL + ObjectScript
IRIS (Graph_KG schema)
```
