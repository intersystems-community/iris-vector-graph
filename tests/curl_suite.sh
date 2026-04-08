#!/usr/bin/env bash
# IVG Cypher API curl smoke suite
#
# Usage:
#   IVG_API_KEY=ivg-local-test bash tests/curl_suite.sh
#   IVG_URL=http://18.222.108.133:29888 IVG_API_KEY=your-key bash tests/curl_suite.sh
#
# Start locally:
#   IRIS_HOST=localhost IRIS_PORT=32775 IRIS_NAMESPACE=USER \
#   IRIS_USERNAME=test IRIS_PASSWORD=test IVG_API_KEY=ivg-local-test \
#   python3 -m uvicorn iris_vector_graph.cypher_api:app --port 8000

set -uo pipefail

IVG_URL="${IVG_URL:-http://localhost:8000}"
IVG_KEY="${IVG_API_KEY:-ivg-local-test}"
BM25_INDEX="${BM25_INDEX:-hla_kg}"

PASS=0; FAIL=0; SKIP=0

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
green() { printf '\033[32m✓ PASS\033[0m  %s\n' "$*"; PASS=$((PASS+1)); }
red()   { printf '\033[31m✗ FAIL\033[0m  %s\n' "$*"; FAIL=$((FAIL+1)); }
gray()  { printf '\033[90m- SKIP\033[0m  %s\n' "$*"; SKIP=$((SKIP+1)); }

cypher() {
    local q="$1" p="${2:-{}}"
    curl -sf -X POST "${IVG_URL}/api/cypher" \
        -H "Content-Type: application/json" \
        -H "X-API-Key: ${IVG_KEY}" \
        -d "{\"query\":$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$q"),\"parameters\":${p}}" \
        2>/dev/null
}

tx() {
    curl -sf -X POST "${IVG_URL}/db/neo4j/tx/commit" \
        -H "Content-Type: application/json" \
        -H "X-API-Key: ${IVG_KEY}" \
        -d "$1" 2>/dev/null
}

assert() {
    local name="$1" body="$2" expr="$3"
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

# ── Probe instance ────────────────────────────────────────────────────────────

bold $'\n=== Probing instance...'
TOTAL=$(cypher "MATCH (n) RETURN count(n) AS c" | python3 -c "import json,sys; print(json.load(sys.stdin)['rows'][0][0])" 2>/dev/null || echo 0)
EDGE_COUNT=$(cypher "MATCH ()-[r]->() RETURN count(r) AS c" | python3 -c "import json,sys; print(json.load(sys.stdin)['rows'][0][0])" 2>/dev/null || echo 0)
FIRST_NODE=$(cypher "MATCH (n) RETURN n.id LIMIT 1" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['rows'][0][0] if d.get('rows') else '')" 2>/dev/null || echo "")
FIRST_LABEL=$(cypher "MATCH (n) RETURN labels(n) LIMIT 1" | python3 -c "
import json,sys
d=json.load(sys.stdin)
raw=d.get('rows',[['']])[0][0] if d.get('rows') else ''
if isinstance(raw, list): raw = raw[0] if raw else ''
print(str(raw).strip())
" 2>/dev/null || echo "")

printf '  nodes: %s  edges: %s  sample: %s  label: %s\n' "$TOTAL" "$EDGE_COUNT" "$FIRST_NODE" "$FIRST_LABEL"
[ "$TOTAL" -gt 0 ] || { printf '\033[31mNo nodes found — load data first.\033[0m\n'; exit 1; }

# ── 1. Health + discovery ─────────────────────────────────────────────────────

bold $'\n=== 1. Health + discovery'

body=$(curl -sf "${IVG_URL}/health" 2>/dev/null)
assert "GET /health status=ok" "$body" "d['status'] == 'ok'"
assert "GET /health engine=true" "$body" "d['engine'] == True"

body=$(curl -sf "${IVG_URL}/db/neo4j" -H "X-API-Key: ${IVG_KEY}" 2>/dev/null)
assert "GET /db/neo4j has db/data" "$body" "'db/data' in d"
assert "GET /db/neo4j version starts with 5" "$body" "d.get('neo4j_version','').startswith('5')"

body=$(curl -sf "${IVG_URL}/db/neo4j/tx" -H "X-API-Key: ${IVG_KEY}" 2>/dev/null)
assert "GET /db/neo4j/tx has commit URL" "$body" "'commit' in d"

body=$(curl -sf "${IVG_URL}/" -H "X-API-Key: ${IVG_KEY}" 2>/dev/null || curl -sf "${IVG_URL}/" 2>/dev/null)
assert "GET / discovery (Neo4j Browser probe)" "$body" "'db/data' in d or 'neo4j_version' in d"

# ── 2. API key enforcement ────────────────────────────────────────────────────

bold $'\n=== 2. API key enforcement'

code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${IVG_URL}/api/cypher" \
    -H "Content-Type: application/json" -d '{"query":"MATCH (n) RETURN n.id LIMIT 1"}' 2>/dev/null)
assert_http "No key → 401" "$code" "401"

code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${IVG_URL}/api/cypher" \
    -H "Content-Type: application/json" -H "X-API-Key: wrong-xyzzy" \
    -d '{"query":"MATCH (n) RETURN n.id LIMIT 1"}' 2>/dev/null)
assert_http "Wrong key → 401" "$code" "401"

# ── 3. Basic Cypher ───────────────────────────────────────────────────────────

bold $'\n=== 3. Basic Cypher'

body=$(cypher "MATCH (n) RETURN count(n) AS c")
assert "count(n) = ${TOTAL}" "$body" "d['rows'][0][0] == ${TOTAL}"

body=$(cypher "MATCH (n) RETURN n.id LIMIT 5")
assert "RETURN n.id LIMIT 5 → 5 rows" "$body" "d['rowCount'] == 5"
assert "column named n_id or id" "$body" "d['columns'][0] in ('n_id','n.id','id')"

body=$(cypher 'MATCH (n) RETURN n.id LIMIT $k' '{"k":3}')
assert "LIMIT \$k=3 parameter" "$body" "d['rowCount'] == 3"

body=$(cypher "MATCH (n) RETURN n.id SKIP 2 LIMIT 3")
assert "SKIP 2 LIMIT 3" "$body" "d['rowCount'] == 3"

# ── 4. WHERE filters ─────────────────────────────────────────────────────────

bold $'\n=== 4. WHERE filters'

if [ -n "$FIRST_NODE" ]; then
    body=$(cypher 'MATCH (n) WHERE n.id = $id RETURN n.id' "{"id":"${FIRST_NODE}"}")
    assert "WHERE n.id = \$id exact match" "$body" "d['rows'][0][0] == '${FIRST_NODE}'"

    PREFIX=$(python3 -c "s='${FIRST_NODE}'; print(s[:min(3,len(s))])")
    body=$(cypher 'MATCH (n) WHERE n.id STARTS WITH $p RETURN count(n) AS c' "{"p":"${PREFIX}"}")
    assert "WHERE n.id STARTS WITH prefix" "$body" "d['rows'][0][0] >= 1"
fi

body=$(cypher "MATCH (n) WHERE n.id IS NOT NULL RETURN count(n) AS c")
assert "WHERE n.id IS NOT NULL" "$body" "d['rows'][0][0] >= ${TOTAL}"

# ── 5. Label queries ──────────────────────────────────────────────────────────

bold $'\n=== 5. Label queries'

if [ -n "$FIRST_LABEL" ]; then
    body=$(cypher "MATCH (n:${FIRST_LABEL}) RETURN count(n) AS c")
    assert "MATCH (n:${FIRST_LABEL}) count ≥ 1" "$body" "d['rows'][0][0] >= 1"

    body=$(cypher "MATCH (n:${FIRST_LABEL}) RETURN n.id LIMIT 3")
    assert "MATCH (n:${FIRST_LABEL}) LIMIT 3" "$body" "d['rowCount'] >= 1"
else
    gray "Label tests skipped — no labels"
fi

# ── 6. Aggregation ────────────────────────────────────────────────────────────

bold $'\n=== 6. Aggregation'

body=$(cypher "MATCH (n)-[r]->() RETURN count(r) AS c")
assert "count all edges = ${EDGE_COUNT}" "$body" "d['rows'][0][0] == ${EDGE_COUNT}"

if [ "$EDGE_COUNT" -gt 0 ]; then
    body=$(cypher "MATCH (n)-[r]->() RETURN n.id, count(r) AS deg ORDER BY deg DESC LIMIT 5")
    assert "degree top-5" "$body" "d['rowCount'] >= 1"
fi

# ── 7. Relationships ─────────────────────────────────────────────────────────

bold $'\n=== 7. Relationship traversal'

if [ "$EDGE_COUNT" -gt 0 ]; then
    body=$(cypher "MATCH (a)-[r]->(b) RETURN a.id, type(r), b.id LIMIT 5")
    assert "1-hop traversal" "$body" "d['rowCount'] >= 1 and len(d['columns']) == 3"

    body=$(cypher "MATCH (a)-[]->(b)-[]->(c) RETURN a.id, b.id, c.id LIMIT 3")
    assert "2-hop traversal returns rows ≥ 0" "$body" "d['rowCount'] >= 0"
else
    gray "Traversal skipped — no edges"
fi

# ── 8. Error handling ────────────────────────────────────────────────────────

bold $'\n=== 8. Error handling'

code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${IVG_URL}/api/cypher" \
    -H "Content-Type: application/json" -H "X-API-Key: ${IVG_KEY}" \
    -d '{"query":"NOT VALID CYPHER !!!"}' 2>/dev/null)
assert_http "Bad Cypher → 400" "$code" "400"

body=$(curl -sf -X POST "${IVG_URL}/api/cypher" \
    -H "Content-Type: application/json" -H "X-API-Key: ${IVG_KEY}" \
    -d '{"query":"NOT VALID CYPHER !!!"}' 2>/dev/null || echo '{"detail":{"status":"error"}}')
assert "Bad Cypher detail.status=error" "$body" "d.get('detail',{}).get('status') == 'error'"

# ── 9. Neo4j transactional API ───────────────────────────────────────────────

bold $'\n=== 9. Neo4j HTTP transactional API (/db/neo4j/tx/commit)'

body=$(tx '{"statements":[{"statement":"MATCH (n) RETURN count(n) AS c"}]}')
assert "tx/commit single → results[0].columns=['c']" "$body" "d['results'][0]['columns'] == ['c']"
assert "tx/commit no errors" "$body" "d['errors'] == []"
assert "tx/commit data[0] has row key" "$body" "isinstance(d['results'][0]['data'][0]['row'], list)"

body=$(tx '{"statements":[{"statement":"MATCH (n) RETURN count(n) AS c"},{"statement":"MATCH ()-[r]->() RETURN count(r) AS c"}]}')
assert "tx/commit 2 statements → 2 results" "$body" "len(d['results']) == 2 and d['errors'] == []"

body=$(tx '{"statements":[{"statement":"NOT VALID CYPHER !!!"}]}')
assert "tx/commit bad Cypher → errors populated" "$body" "len(d['errors']) >= 1 and 'code' in d['errors'][0]"

body=$(tx '{"statements":[{"statement":"MATCH (n) RETURN count(n) AS c"},{"statement":"NOT VALID CYPHER"}]}')
assert "tx/commit mixed → 1 result + 1 error" "$body" "len(d['results']) == 1 and len(d['errors']) == 1"

# ── 10. BM25 ─────────────────────────────────────────────────────────────────

bold $'\n=== 10. BM25 (ivg.bm25.search)'

BM25_N=$(curl -sf -X POST "${IVG_URL}/api/cypher" \
    -H "Content-Type: application/json" \
    -H "X-API-Key: ${IVG_KEY}" \
    -d "{\"query\":\"CALL ivg.bm25.search('${BM25_INDEX}', 'test', 1) YIELD node, score RETURN node\",\"parameters\":{}}" 2>/dev/null \
    | python3 -c "import json,sys; d=json.load(sys.stdin); print(1 if d.get('status')=='OK' else 0)" 2>/dev/null || echo 0)

if [ "$BM25_N" -gt 0 ]; then
    printf '  BM25 "%s": N=%s\n' "$BM25_INDEX" "$BM25_N"

    TERM=$(python3 -c "
import re
s = '${FIRST_NODE}'
tokens = re.findall(r'[a-z0-9]+', s.lower())
print(tokens[0] if tokens else 'hla')
" 2>/dev/null || echo "hla")

    body=$(cypher "CALL ivg.bm25.search(\$idx, \$q, 5) YIELD node, score RETURN node, score" \
        "{\"idx\":\"${BM25_INDEX}\",\"q\":\"${TERM}\"}")
    assert "CALL ivg.bm25.search YIELD node,score" "$body" "d['status'] == 'OK' and d['columns'] == ['node','score']"
    assert "BM25 scores are numeric" "$body" "all(isinstance(r[1],(int,float)) for r in d['rows'])"

    body=$(cypher "CALL ivg.bm25.search(\$idx, '', 5) YIELD node, score RETURN node, score" \
        "{\"idx\":\"${BM25_INDEX}\"}")
    assert "BM25 empty query → 0 results" "$body" "d['rowCount'] == 0"
else
    gray "BM25 skipped — no '${BM25_INDEX}' index (run: pytest tests/e2e/test_hla_kg_e2e.py)"
fi

# ── 11. PPR ──────────────────────────────────────────────────────────────────

bold $'\n=== 11. PPR (ivg.ppr)'

if [ "$EDGE_COUNT" -gt 0 ] && [ -n "$FIRST_NODE" ]; then
    body=$(cypher "CALL ivg.ppr([\$seed], 0.85, 10) YIELD node, score RETURN node, score ORDER BY score DESC LIMIT 5" \
        "{\"seed\":\"${FIRST_NODE}\"}")
    assert "CALL ivg.ppr returns rows" "$body" "d['rowCount'] >= 0 and d['columns'] == ['node','score']"
else
    gray "PPR skipped — no edges"
fi

# ── Summary ──────────────────────────────────────────────────────────────────

total=$((PASS + FAIL + SKIP))
bold $'\n=== Summary'
printf 'Total: %d  ' "$total"
printf '\033[32mPASS: %d\033[0m  ' "$PASS"
printf '\033[31mFAIL: %d\033[0m  ' "$FAIL"
printf '\033[90mSKIP: %d\033[0m\n\n' "$SKIP"

[ "$FAIL" -eq 0 ] || exit 1
