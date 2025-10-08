# Quickstart: Interactive IRIS Demo Web Interface

**Feature**: 005-interactive-demo-web
**Purpose**: Validate demo server implementation through end-to-end walkthrough

## Prerequisites

1. **Fraud detection API running**:
   ```bash
   docker-compose -f docker-compose.fraud-embedded.yml up -d
   # Wait ~2 min for startup
   curl http://localhost:8100/fraud/health  # Should return {"status": "healthy"}
   ```

2. **Biomedical graph backend available**:
   ```bash
   docker-compose -f docker-compose.acorn.yml up -d
   # OR: docker-compose up -d (Community IRIS)
   ```

3. **Demo server started**:
   ```bash
   docker-compose -f docker-compose.demo.yml up -d
   # Wait ~30 sec for startup
   ```

## Demo Walkthrough

### Step 1: Access Demo UI

**Action**: Open http://localhost:8200 in browser

**Expected**:
- ✅ Homepage loads within 2 seconds
- ✅ Two tabs visible: "Financial Services" and "Biomedical Research"
- ✅ Default tab: "Financial Services" (fraud demo)
- ✅ Educational tooltips present for IRIS features

**Validation**:
```bash
curl -s http://localhost:8200 | grep -q "Financial Services"
echo "✅ Demo homepage loaded"
```

---

### Step 2: Fraud Demo - Transaction Scoring

**Tab**: Financial Services

**Action**: Submit sample transaction for fraud scoring

**Input**:
- Payer: `acct:demo_user_001`
- Amount: `1500.00`
- Device: `dev:laptop_chrome`
- Merchant: `merch:electronics_store`
- IP Address: `192.168.1.100`

**Expected** (FR-002: <2s response):
- ✅ Fraud probability displayed (e.g., 0.15 = "low risk")
- ✅ Risk classification shown: low/medium/high/critical
- ✅ Contributing factors listed (e.g., "Normal amount", "Known device")
- ✅ Performance metrics visible: `~10ms execution time`, `backend: fraud_api`
- ✅ Query added to session history

**API Validation**:
```bash
curl -X POST http://localhost:8200/api/fraud/score \
  -H 'Content-Type: application/json' \
  -d '{
    "payer": "acct:demo_user_001",
    "amount": 1500.00,
    "device": "dev:laptop_chrome",
    "merchant": "merch:electronics_store",
    "ip_address": "192.168.1.100"
  }' | jq '.result.risk_classification'

# Expected: "low" or "medium"
```

---

### Step 3: Fraud Demo - Bitemporal Time-Travel

**Tab**: Financial Services → Bitemporal Query

**Action**: Perform "what did we know at approval time?" query

**Input**:
- Event ID: `txn_2025_001` (from pre-loaded demo data)
- System Time (when we knew): `2025-01-15 14:00:00`

**Expected** (FR-008):
- ✅ Historical fraud score displayed as it was at 14:00
- ✅ Status at that time shown (e.g., "clean")
- ✅ Side-by-side comparison with current state
- ✅ Highlighted differences (score changed, status changed)
- ✅ Temporal context clearly indicated ("As of Jan 15, 2:00 PM")

**Validation**:
```bash
curl -X POST http://localhost:8200/api/fraud/bitemporal \
  -H 'Content-Type: application/json' \
  -d '{
    "event_id": "txn_2025_001",
    "system_time": "2025-01-15T14:00:00Z",
    "query_mode": "as_of"
  }' | jq '.result.fraud_score'

# Expected: historical score (e.g., 0.12)
```

---

### Step 4: Fraud Demo - Audit Trail

**Tab**: Financial Services → Audit Trail

**Action**: View complete version history for transaction

**Input**:
- Event ID: `txn_2025_001`

**Expected** (FR-009):
- ✅ All versions displayed in timeline (v1, v2, v3, v4)
- ✅ Each version shows:
  - Timestamp (system_from)
  - Fraud score at that version
  - Status at that version
  - Who made the change
  - Reason for change
- ✅ Visual timeline with event markers (clickable)
- ✅ Drill-down into specific version shows full data snapshot
- ✅ Chargeback workflow visible: clean → suspicious → confirmed_fraud → reversed (FR-011)

**API Validation**:
```bash
curl http://localhost:8200/api/fraud/audit/txn_2025_001 | jq '.versions | length'

# Expected: 4 (four versions)
```

---

### Step 5: Switch to Biomedical Tab

**Action**: Click "Biomedical Research" tab

**Expected** (FR-021: preserve context):
- ✅ Tab switches without page reload (HTMX swap)
- ✅ Session context preserved (query history still accessible)
- ✅ Biomedical demo UI loads (<100ms swap)
- ✅ Protein search form displayed

**Validation**:
```bash
# Check session history preserves fraud queries
curl http://localhost:8200/api/session/history | jq '.queries | length'

# Expected: > 0 (fraud queries still in history)
```

---

### Step 6: Biomedical Demo - Protein Search

**Tab**: Biomedical Research

**Action**: Search for similar proteins

**Input**:
- Protein: `TP53`
- Search mode: `Hybrid` (vector + text + RRF)
- Top K: `10`

**Expected** (FR-013, FR-014, FR-017):
- ✅ Top 10 results displayed within 2 seconds
- ✅ Results ranked by similarity score (descending)
- ✅ Each result shows:
  - Protein name and ID
  - Similarity score (0.0-1.0)
  - Interaction count
  - Search method (RRF_fusion, vector, text)
- ✅ Performance metrics: `execution_time < 200ms`, `search_methods: [HNSW_vector, BM25_text, RRF_fusion]`
- ✅ Educational tooltip explaining RRF fusion

**API Validation**:
```bash
curl -X POST http://localhost:8200/api/bio/search \
  -H 'Content-Type: application/json' \
  -d '{
    "protein_identifier": "TP53",
    "search_mode": "hybrid",
    "top_k": 10
  }' | jq '.results[0].protein_name'

# Expected: "TP53" (exact match first)
```

---

### Step 7: Biomedical Demo - Pathway Visualization

**Tab**: Biomedical Research → Pathway Query

**Action**: Find interaction pathway between two proteins

**Input**:
- Source protein: `TP53`
- Target protein: `MDM2`
- Max hops: `5`

**Expected** (FR-016):
- ✅ Shortest path computed and displayed
- ✅ Interactive D3.js graph visualization loads
- ✅ Pathway nodes highlighted (TP53 → ... → MDM2)
- ✅ Intermediate proteins labeled with hop number
- ✅ Interaction types shown on edges (binds, activates, etc.)
- ✅ Graph responsive (zoom, pan, drag nodes)

**Validation**:
```bash
curl -X POST http://localhost:8200/api/bio/pathway \
  -H 'Content-Type: application/json' \
  -d '{
    "source_protein": "TP53",
    "target_protein": "MDM2",
    "max_hops": 5,
    "algorithm": "shortest_path"
  }' | jq '.pathway | length'

# Expected: <= 5 (pathway found within max hops)
```

---

### Step 8: Biomedical Demo - Network Node Expansion

**Tab**: Biomedical Research (graph visualization active)

**Action**: Click on protein node in graph to expand neighbors

**Input**:
- Click on `TP53` node in visualization

**Expected** (FR-018):
- ✅ HTMX request triggered to `/api/bio/network/TP53/expand`
- ✅ Additional neighbor proteins loaded (e.g., 20 neighbors)
- ✅ New nodes added to D3 graph (smooth animation)
- ✅ New edges drawn connecting to TP53
- ✅ Graph auto-adjusts layout (force simulation resumes)
- ✅ Performance: <500ms to load and render neighbors

**API Validation**:
```bash
curl "http://localhost:8200/api/bio/network/TP53/expand?max_neighbors=20" | jq '.neighbors | length'

# Expected: 20 (or fewer if TP53 has <20 interactions)
```

---

### Step 9: Session Management - Query History

**Action**: View all queries performed in session

**Input**:
- Click "History" button (or navigate to session management)

**Expected** (FR-003):
- ✅ All queries from both fraud and biomedical tabs displayed
- ✅ Query types labeled (fraud_score, bitemporal, protein_search, etc.)
- ✅ Timestamps for each query
- ✅ Quick links to re-run queries
- ✅ Performance summary stats (avg execution time, total queries)

**Validation**:
```bash
curl http://localhost:8200/api/session/history | jq '{
  total: .total_count,
  fraud_queries: [.queries[] | select(.query_type | startswith("fraud"))] | length,
  bio_queries: [.queries[] | select(.query_type == "protein_search" or .query_type == "pathway")] | length
}'

# Expected: fraud_queries >= 3, bio_queries >= 2
```

---

### Step 10: Export Demo Results

**Tab**: Session Management

**Action**: Export session results in JSON format

**Input**:
- Format: `JSON`
- Include metrics: `true`

**Expected** (FR-023):
- ✅ JSON file downloaded with all query results
- ✅ File includes:
  - Session metadata
  - All query parameters and results
  - Performance metrics for each query
  - Timestamp data
- ✅ File size reasonable (<5MB for typical demo session)
- ✅ JSON valid and parseable

**Validation**:
```bash
curl -X POST http://localhost:8200/api/session/export \
  -H 'Content-Type: application/json' \
  -d '{
    "format": "json",
    "include_metrics": true
  }' | jq '.queries | length'

# Expected: >= 5 (all queries from session)
```

---

## Success Criteria Summary

| Requirement | Test Step | Status |
|-------------|-----------|--------|
| FR-001: Separate demo modes | Step 1, 5 | ✅ |
| FR-002: <2s response time | Step 2, 6 | ✅ |
| FR-003: Preserve session state | Step 5, 9 | ✅ |
| FR-006: Submit fraud transaction | Step 2 | ✅ |
| FR-007: Risk classification | Step 2 | ✅ |
| FR-008: Bitemporal time-travel | Step 3 | ✅ |
| FR-009: Complete audit trail | Step 4 | ✅ |
| FR-011: Chargeback workflow | Step 4 | ✅ |
| FR-013: Protein search | Step 6 | ✅ |
| FR-014: Similarity ranking | Step 6 | ✅ |
| FR-016: Pathway queries | Step 7 | ✅ |
| FR-017: Hybrid search | Step 6 | ✅ |
| FR-018: Node expansion | Step 8 | ✅ |
| FR-021: Switch modes without losing context | Step 5 | ✅ |
| FR-023: Export results | Step 10 | ✅ |

---

## Troubleshooting

### Demo server won't start
```bash
# Check if ports are available
lsof -i :8200  # Demo server port should be free

# Check backend services
curl http://localhost:8100/fraud/health
docker ps | grep iris
```

### Slow responses (>2s)
```bash
# Check backend health
curl http://localhost:8100/fraud/health
docker logs iris-fraud-embedded --tail 50

# Enable demo mode for cached data
export DEMO_MODE=true
docker-compose -f docker-compose.demo.yml restart
```

### Graph visualization not loading
```bash
# Check browser console for JavaScript errors
# Verify D3.js library loaded (view page source)

# Test API endpoint directly
curl http://localhost:8200/api/bio/network/TP53/expand
```

---

**Quickstart Complete**: All functional requirements validated through end-to-end demo walkthrough.
