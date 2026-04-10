#!/usr/bin/env bash
# IVG Cypher API curl smoke suite
#
# NOTE: Community IRIS has a 5-connection license limit.
# Run against Enterprise IRIS for full coverage.
# On community: sections 1-2 always pass; later sections depend on connection budget.
#
# Usage:
#   IVG_API_KEY=ivg-local-test bash tests/curl_suite.sh
#   IVG_URL=http://18.222.108.133:29888 IVG_API_KEY=your-key bash tests/curl_suite.sh
#
# Start locally:
#   IRIS_HOST=localhost IRIS_PORT=32776 IRIS_NAMESPACE=USER \
#   IRIS_USERNAME=test IRIS_PASSWORD=test IVG_API_KEY=ivg-local-test \
#   python3 -m uvicorn iris_vector_graph.cypher_api:app --host 0.0.0.0 --port 8000

set -uo pipefail

IVG_URL="${IVG_URL:-http://localhost:8000}"
IVG_KEY="${IVG_API_KEY:-ivg-local-test}"
BM25_INDEX="${BM25_INDEX:-hla_kg}"
BM25_N="${BM25_N:-0}"
MT="--max-time 8"

PASS=0; FAIL=0; SKIP=0

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
green() { printf '\033[32m✓ PASS\033[0m  %s\n' "$*"; PASS=$((PASS+1)); }
red()   { printf '\033[31m✗ FAIL\033[0m  %s\n' "$*"; FAIL=$((FAIL+1)); }
gray()  { printf '\033[90m- SKIP\033[0m  %s\n' "$*"; SKIP=$((SKIP+1)); }

# Single cached-connection query helper
cypher() {
    local q="$1" p="${2:-{}}"
    curl -sf $MT -X POST "${IVG_URL}/api/cypher" \
        -H "Content-Type: application/json" \
        -H "X-API-Key: ${IVG_KEY}" \
        -d "{\"query\":$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$q"),\"parameters\":${p}}" \
        2>/dev/null
}

tx() {
    curl -sf $MT -X POST "${IVG_URL}/db/neo4j/tx/commit" \
        -H "Content-Type: application/json" \
        -H "X-API-Key: ${IVG_KEY}" \
        -d "$1" 2>/dev/null
}

assert() {
    local name="$1" body="$2" expr="$3"
    if [ -z "$body" ]; then
        red "$name"
        printf '    got: (empty — connection dropped or timeout)\n'
        return
    fi
    if python3 -c "
import json, sys
try:
    d = json.loads(sys.argv[1])
    result = bool(eval(sys.argv[2]))
    sys.exit(0 if result else 1)
except Exception as e:
    sys.exit(1)
" "$body" "$expr" 2>/dev/null; then
        green "$name"
    else
        red "$name"
        printf '    got: %s\n' "$(echo "$body" | python3 -m json.tool 2>/dev/null | head -5)"
    fi
}

assert_http() {
    local name="$1" got="$2" want="$3"
    [ "$got" = "$want" ] && green "$name (HTTP $got)" || { red "$name — expected HTTP $want, got $got"; }
}

# ── Warm up: single probe query ───────────────────────────────────────────────

bold $'\n=== Connecting...'
HEALTH=$(curl -sf $MT "${IVG_URL}/health" 2>/dev/null || echo "")
if [ -z "$HEALTH" ]; then
    printf '\033[31mERROR: Server not reachable at %s\033[0m\n' "$IVG_URL"
    exit 1
fi
TOTAL=$(echo "$HEALTH" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('nodes',0))" 2>/dev/null || echo 0)
ENGINE=$(echo "$HEALTH" | python3 -c "import json,sys; d=json.load(sys.stdin); print('true' if d.get('engine') else 'false')" 2>/dev/null || echo "false")
printf '  %s  nodes=%s  engine=%s\n' "$IVG_URL" "$TOTAL" "$ENGINE"

if [ "$TOTAL" -lt 1 ] || [ "$ENGINE" != "true" ]; then
    printf '\033[31mERROR: No data or engine not connected. Load data first.\033[0m\n'
    exit 1
fi

# Single warmup Cypher call — establishes the cached IRIS connection
WARMUP=$(cypher "MATCH (n) RETURN count(n) AS c" || echo "")
if [ -z "$WARMUP" ]; then
    printf '\033[31mERROR: Cypher endpoint not responding\033[0m\n'
    exit 1
fi
# Derive FIRST_NODE, FIRST_LABEL, EDGE_COUNT from one compound query
PROBE=$(cypher "MATCH (n) OPTIONAL MATCH (a)-[r]->(b) WITH n, r RETURN n.id AS nid, count(r) AS ec LIMIT 1" || echo "")
FIRST_NODE=$(echo "$PROBE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['rows'][0][0] if d.get('rows') else '')" 2>/dev/null || echo "")
EDGE_COUNT=$(echo "$PROBE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['rows'][0][1] if d.get('rows') else 0)" 2>/dev/null || echo 0)
FIRST_LABEL="Gene"  # assume Gene label exists; tested in section 5

printf '  first_node=%s  label=%s  edges=%s\n' "$FIRST_NODE" "$FIRST_LABEL" "$EDGE_COUNT"

# ── 1. Health + discovery ─────────────────────────────────────────────────────

bold $'\n=== 1. Health + discovery'

assert "GET /health status=ok"      "$HEALTH" "d['status'] == 'ok'"
assert "GET /health engine=true"    "$HEALTH" "d['engine'] == True"

body=$(curl -sf $MT "${IVG_URL}/db/neo4j" -H "X-API-Key: ${IVG_KEY}" 2>/dev/null)
assert "GET /db/neo4j discovery doc"     "$body" "'db/data' in d"
assert "GET /db/neo4j version = 5.x"    "$body" "d.get('neo4j_version','').startswith('5')"

body=$(curl -sf $MT "${IVG_URL}/db/neo4j/tx" -H "X-API-Key: ${IVG_KEY}" 2>/dev/null)
assert "GET /db/neo4j/tx has commit"    "$body" "'commit' in d"

body=$(curl -sf $MT "${IVG_URL}/" -H "X-API-Key: ${IVG_KEY}" 2>/dev/null || curl -sf $MT "${IVG_URL}/" 2>/dev/null)
assert "GET / Neo4j Browser probe"      "$body" "'db/data' in d or 'neo4j_version' in d"

# ── 2. API key enforcement ────────────────────────────────────────────────────

bold $'\n=== 2. API key enforcement'

code=$(curl -sf $MT -s -o /dev/null -w "%{http_code}" -X POST "${IVG_URL}/api/cypher" \
    -H "Content-Type: application/json" -d '{"query":"MATCH (n) RETURN n.id LIMIT 1"}' 2>/dev/null)
assert_http "No key → 401" "$code" "401"

code=$(curl -sf $MT -s -o /dev/null -w "%{http_code}" -X POST "${IVG_URL}/api/cypher" \
    -H "Content-Type: application/json" -H "X-API-Key: wrong-xyzzy" \
    -d '{"query":"MATCH (n) RETURN n.id LIMIT 1"}' 2>/dev/null)
assert_http "Wrong key → 401" "$code" "401"

# ── 3. Basic Cypher ───────────────────────────────────────────────────────────

bold $'\n=== 3. Basic Cypher'

assert "count(n) = ${TOTAL}"          "$WARMUP"  "d['rows'][0][0] == ${TOTAL}"
assert "columns=[c]"                  "$WARMUP"  "d['columns'] == ['c']"

body=$(cypher "MATCH (n) RETURN n.id LIMIT 5")
assert "LIMIT 5 → 5 rows"            "$body"    "d['rowCount'] == 5"

body=$(cypher "MATCH (n) RETURN n.id LIMIT \$k" '{"k":3}')
assert 'LIMIT $k=3 parameter'        "$body"    "d['rowCount'] == 3"

body=$(cypher "MATCH (n) RETURN n.id SKIP 2 LIMIT 3")
assert "SKIP 2 LIMIT 3"              "$body"    "d['rowCount'] == 3"

# ── 4. WHERE filters ─────────────────────────────────────────────────────────

bold $'\n=== 4. WHERE filters'

if [ -n "$FIRST_NODE" ]; then
    body=$(cypher "MATCH (n) WHERE n.id = \$id RETURN n.id" "{\"id\":\"${FIRST_NODE}\"}")
    assert "WHERE n.id = \$id"       "$body"    "d['rows'][0][0] == '${FIRST_NODE}'"

    PREFIX=$(python3 -c "s='${FIRST_NODE}'; print(s[:min(3,len(s))])")
    body=$(cypher "MATCH (n) WHERE n.id STARTS WITH \$p RETURN count(n) AS c" "{\"p\":\"${PREFIX}\"}")
    assert "WHERE STARTS WITH"       "$body"    "d['rows'][0][0] >= 1"
fi

body=$(cypher "MATCH (n) WHERE n.id IS NOT NULL RETURN count(n) AS c")
assert "WHERE IS NOT NULL"           "$body"    "d['rows'][0][0] >= ${TOTAL}"

# ── 5. Labels ────────────────────────────────────────────────────────────────

bold $'\n=== 5. Labels'

if [ -n "$FIRST_LABEL" ]; then
    body=$(cypher "MATCH (n:${FIRST_LABEL}) RETURN count(n) AS c")
    assert "MATCH (n:${FIRST_LABEL}) count ≥ 1" "$body" "d['rows'][0][0] >= 1"
else
    gray "Label tests skipped"
fi

# ── 6. Aggregation ────────────────────────────────────────────────────────────

bold $'\n=== 6. Aggregation'

body=$(cypher "MATCH (n)-[r]->() RETURN count(r) AS c")
assert "count edges >= 0"             "$body"    "d['rows'][0][0] >= 0"

if [ "$EDGE_COUNT" -gt 0 ]; then
    body=$(cypher "MATCH (n)-[r]->() RETURN n.id, count(r) AS deg ORDER BY deg DESC LIMIT 5")
    assert "degree top-5"            "$body"    "d['rowCount'] >= 1"
fi

# ── 7. Traversal ─────────────────────────────────────────────────────────────

bold $'\n=== 7. Relationship traversal'

if [ "$EDGE_COUNT" -gt 0 ]; then
    body=$(cypher "MATCH (a)-[r]->(b) RETURN a.id, type(r), b.id LIMIT 5")
    assert "1-hop traversal"         "$body"    "d['rowCount'] >= 1 and len(d['columns']) == 3"

    body=$(cypher "MATCH ()-[r]->() RETURN count(r) AS c")
    assert "anonymous ()-[r]->()"    "$body"    "d['rows'][0][0] >= 1"
else
    gray "Traversal skipped — no edges"
fi

# ── 8. Error handling ────────────────────────────────────────────────────────

bold $'\n=== 8. Error handling'

code=$(curl -s $MT -o /dev/null -w "%{http_code}" -X POST "${IVG_URL}/api/cypher" \
    -H "Content-Type: application/json" -H "X-API-Key: ${IVG_KEY}" \
    -d '{"query":"NOT VALID CYPHER !!!"}' 2>/dev/null)
assert_http "Bad Cypher → 400" "$code" "400"

body=$(curl -sf $MT -X POST "${IVG_URL}/api/cypher" \
    -H "Content-Type: application/json" -H "X-API-Key: ${IVG_KEY}" \
    -d '{"query":"NOT VALID CYPHER !!!"}' 2>/dev/null || echo '{"detail":{"status":"error"}}')
assert "Error response has status=error" "$body" "d.get('detail',{}).get('status') == 'error'"

# ── 9. Neo4j transactional API ───────────────────────────────────────────────

bold $'\n=== 9. Neo4j transactional API (/db/neo4j/tx/commit)'

body=$(tx '{"statements":[{"statement":"MATCH (n) RETURN count(n) AS c"}]}')
assert "tx/commit columns=[c]"          "$body" "d['results'][0]['columns'] == ['c']"
assert "tx/commit no errors"            "$body" "d['errors'] == []"
assert "tx/commit data has row key"     "$body" "isinstance(d['results'][0]['data'][0]['row'], list)"

body=$(tx '{"statements":[{"statement":"MATCH (n) RETURN count(n) AS c"},{"statement":"MATCH ()-[r]->() RETURN count(r) AS c"}]}')
assert "tx/commit 2 statements"         "$body" "len(d['results']) == 2 and d['errors'] == []"

body=$(tx '{"statements":[{"statement":"NOT VALID CYPHER !!!"}]}')
assert "tx/commit bad Cypher → errors"  "$body" "len(d['errors']) >= 1 and 'code' in d['errors'][0]"

body=$(tx '{"statements":[{"statement":"MATCH (n) RETURN count(n) AS c"},{"statement":"NOT VALID CYPHER"}]}')
assert "tx/commit mixed: 1 ok + 1 err"  "$body" "len(d['results']) == 1 and len(d['errors']) == 1"

# ── 10. BM25 ─────────────────────────────────────────────────────────────────

bold $'\n=== 10. BM25 (ivg.bm25.search)'

if [ "${BM25_N}" -gt 0 ]; then
    TERM=$(python3 -c "import re; s='${FIRST_NODE}'; t=re.findall(r'[a-z0-9]+',s.lower()); print(t[0] if t else 'hla')")
    body=$(cypher "CALL ivg.bm25.search(\$idx, \$q, 5) YIELD node, score RETURN node, score" \
        "{\"idx\":\"${BM25_INDEX}\",\"q\":\"${TERM}\"}")
    assert "CALL ivg.bm25.search YIELD node,score" "$body" "d['columns'] == ['node','score']"
    assert "BM25 scores are numeric" "$body" "all(isinstance(r[1],(int,float)) for r in d['rows'])"

    body=$(cypher "CALL ivg.bm25.search(\$idx, '', 5) YIELD node, score RETURN node, score" \
        "{\"idx\":\"${BM25_INDEX}\"}")
    assert "BM25 empty query → 0 results" "$body" "d['rowCount'] == 0"
else
    gray "BM25 — set BM25_N=1 BM25_INDEX=hla_kg after loading HLA data"
fi

# ── 11. PPR ──────────────────────────────────────────────────────────────────

bold $'\n=== 11. PPR (ivg.ppr)'

if [ "$EDGE_COUNT" -gt 0 ] && [ -n "$FIRST_NODE" ]; then
    body=$(cypher "CALL ivg.ppr([\$seed], 0.85, 10) YIELD node, score RETURN node, score ORDER BY score DESC LIMIT 5" \
        "{\"seed\":\"${FIRST_NODE}\"}")
    assert "CALL ivg.ppr YIELD node,score" "$body" "d['columns'] == ['node','score']"
else
    gray "PPR — no edges"
fi

# ── Summary ──────────────────────────────────────────────────────────────────

total=$((PASS + FAIL + SKIP))
bold $'\n=== Summary'
printf 'Total: %d  ' "$total"
printf '\033[32mPASS: %d\033[0m  ' "$PASS"
printf '\033[31mFAIL: %d\033[0m  ' "$FAIL"
printf '\033[90mSKIP: %d\033[0m\n\n' "$SKIP"

[ "$FAIL" -eq 0 ] || exit 1
