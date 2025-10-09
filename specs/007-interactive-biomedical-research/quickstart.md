# Quickstart: Interactive Biomedical Research Demo

**Feature**: 007-interactive-biomedical-research
**Purpose**: Step-by-step validation of demo functionality
**Time**: ~5 minutes

---

## Prerequisites

1. **Demo server running**:
   ```bash
   python src/iris_demo_server/app.py
   ```
   Should show: `INFO: Uvicorn running on http://0.0.0.0:8200`

2. **Browser open**: Navigate to `http://localhost:8200`

3. **(Optional) Biomedical backend running**:
   - If available: Demo uses live data
   - If not: Circuit breaker falls back to demo mode (cached proteins)

---

## Test Scenario 1: Protein Similarity Search

**Requirement**: FR-006, FR-007 (protein search with similarity scores)

1. Click homepage link: **"View biomedical demo"** → Navigates to `/bio`
2. Select scenario: **"💊 Cancer Protein Research"** button
3. Verify form pre-filled with: `query_text="TP53", query_type="name", top_k=10`
4. Click **"Search Proteins"** button
5. **Expected**:
   - Results appear in <2 seconds (FR-002)
   - Table shows 10 proteins with similarity scores 0.0-1.0
   - Top result is TP53 itself (score = 1.0)
   - Performance metrics show execution time + backend status

**Success Criteria**: ✅ Search completes <2s, 10 results displayed

---

## Test Scenario 2: Network Visualization

**Requirement**: FR-012, FR-013 (interactive network graph)

1. From search results (above), click **first protein node** (TP53)
2. **Expected**:
   - D3.js force-directed graph renders below results
   - Shows TP53 as center node + 5-10 interaction partners
   - Nodes are draggable
   - Zoom/pan controls work
   - Edge labels show interaction types (inhibition, activation)

3. Click **MDM2 node** in graph
4. **Expected**:
   - Graph expands to show MDM2's neighbors
   - New nodes animate into view
   - Total nodes <500 (FR-018 hard limit)

**Success Criteria**: ✅ Graph renders, nodes expand on click

---

## Test Scenario 3: Pathway Analysis

**Requirement**: FR-019, FR-020 (shortest pathway between proteins)

1. Select scenario: **"🧬 Metabolic Pathway"** button
2. Verify form shows: `source="GAPDH", target="LDHA", max_hops=2`
3. Click **"Find Pathway"** button
4. **Expected**:
   - Results show pathway: GAPDH → (intermediate) → LDHA
   - Path highlighted in network graph (if visible)
   - Confidence score 0.0-1.0 displayed
   - Pathway edges labeled with interaction types

**Success Criteria**: ✅ Pathway found, confidence score shown

---

## Test Scenario 4: Hybrid Search

**Requirement**: FR-025, FR-026 (vector + text fusion)

1. Clear search form, enter: `query_text="tumor suppressor", query_type="function"`
2. Click **"Search Proteins"**
3. **Expected**:
   - Results combine vector similarity + text matching
   - `search_method` shows "hybrid" in metrics
   - Results include TP53, BRCA1, PTEN (known tumor suppressors)

**Success Criteria**: ✅ Hybrid search returns relevant proteins

---

## Test Scenario 5: Demo Mode Fallback

**Requirement**: Circuit breaker resilience (Constitution VII)

1. Stop biomedical backend (if running): `pkill -f biomedical_engine`
2. Perform search for "TP53"
3. **Expected**:
   - Search still works (circuit breaker opens after 5 failures)
   - Metrics show `backend_used="cached_demo"`
   - Notice: "Using demo mode - backend unavailable"
   - Results from fixture data (10-15 sample proteins)

**Success Criteria**: ✅ Demo continues working in fallback mode

---

## Test Scenario 6: Real-Time Filtering

**Requirement**: FR-027 (filter by organism, confidence)

1. Perform search for "kinase"
2. Apply filter: **Organism: "Homo sapiens"**
3. **Expected**:
   - Results update via HTMX (no page reload)
   - Only human proteins shown
   - Result count updates in metrics

**Success Criteria**: ✅ Filtering works without page refresh

---

## Test Scenario 7: Performance Validation

**Requirement**: FR-002 (<2s response time)

1. Open browser DevTools → Network tab
2. Perform protein search for "TP53"
3. Check timing for `/api/bio/search` request
4. **Expected**:
   - Request completes in <2000ms
   - Metrics JSON shows `execution_time_ms < 2000`

**Success Criteria**: ✅ All searches <2 seconds

---

## Validation Checklist

After completing all scenarios:

- [ ] Homepage /bio link works
- [ ] 3 demo scenarios load correctly
- [ ] Protein search returns 10 results <2s
- [ ] D3.js network graph renders and is interactive
- [ ] Node expansion works (click to expand neighbors)
- [ ] Pathway search finds multi-hop paths
- [ ] Hybrid search combines vector + text
- [ ] Demo mode fallback works when backend unavailable
- [ ] Organism filtering updates results via HTMX
- [ ] All operations meet <2s performance requirement
- [ ] Visual styling matches fraud demo quality

---

## Troubleshooting

**Issue**: Demo server won't start
- **Fix**: Check port 8200 not already in use: `lsof -i :8200`

**Issue**: No results from search
- **Fix**: Check biomedical backend running on port 8300, or verify demo mode active

**Issue**: Graph doesn't render
- **Fix**: Check browser console for D3.js errors, verify D3.js CDN loaded

**Issue**: HTMX not updating
- **Fix**: Verify HTMX CDN loaded, check `hx-` attributes in HTML

---

**Quickstart Complete**: All scenarios validated. Ready for /tasks phase.
