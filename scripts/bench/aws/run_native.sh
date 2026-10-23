#!/usr/bin/env bash
# The native Cypher executor (spec 253) on the run instance: deploy this checkout's classes into an already loaded
# namespace and run the SNB queries there twice, back to back, with the shipped defaults (native on) and on the SQL
# path (IVG_NATIVE_CYPHER=0). Results under results/aws-native-<tag>, beside run_snb.sh's Neo4j and Memgraph timings
# for the same scale (results/aws-<scale tag>).
# Usage: [MODES="native sql"] run_native.sh <tag> <namespace> <data-root> [cap-s]
#   run_native.sh sf1  SNB1B  ~/.cache/ivg-bench/snb1/out  600
#   MODES=native run_native.sh sf10 SNB10B ~/.cache/ivg-bench/snb10/out 600   (the chain's side B timed SQL already)
set -uo pipefail
TAG="$1"; NS="$2"; ROOT="$3"; CAP="${4:-120}"
cd /Users/tdyar/ws/iris-vector-graph
PY=.venv/bin/python
OUT="docs/performance/alt-bench/results/aws-native-$TAG"; mkdir -p "$OUT"
git rev-parse --short HEAD >"$OUT/IVG_COMMIT"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$OUT/run.log"; }
# Results leave the box after every step (see run_snb.sh); pull.sh on the laptop is the second copy.
push() { [[ -n "${BUCKET:-}" ]] && { aws s3 sync "$OUT" "s3://$BUCKET/results/aws-native-$TAG" --only-show-errors || log "s3 push failed (rc=$?)"; }; return 0; }

log "deploy $NS"
# the instance's python3 has no IRIS driver: the deploy uses the repo's .venv; a failed deploy stops the run, since
# classes older than the planner keep native off and the "native" leg would time the SQL path
PATH="$PWD/.venv/bin:$PATH" IVG_PORT=31972 IVG_NAMESPACE="$NS" scripts/enterprise-container.sh tcp-deploy >>"$OUT/deploy.log" 2>&1 || { log "deploy rc=$? (stopping)"; exit 1; }
# the native gate needs the adjacency index declared; initialize_schema declares it (and is a no-op where it is)
log "schema"
PYTHONPATH=. IVG_IGNORE_NAMESPACE_CHECK=1 $PY - "$NS" >>"$OUT/deploy.log" 2>&1 <<'EOF' || { log "schema rc=$? (no adjacency index or a stale executor: stopping)"; exit 1; }
import sys, iris
from iris_vector_graph.engine import IRISGraphEngine
e = IRISGraphEngine(iris.connect("localhost", 31972, sys.argv[1], "_SYSTEM", "SYS"), embedding_dimension=768)
print("adjacency indexed:", e._adjacency_indexed, "executor current:", e._native_executor_current())
sys.exit(0 if e._adjacency_indexed and e._native_executor_current() else 3)
EOF
push
for mode in ${MODES:-native sql}; do
  log "$mode queries"
  PYTHONPATH=. IVG_NATIVE_CYPHER=$([[ $mode == native ]] && echo 1 || echo 0) IVG_IGNORE_NAMESPACE_CHECK=1 \
    $PY scripts/bench/alt/snb_worker.py query --ns "$NS" --root "$ROOT" --cap-s "$CAP" --out "$OUT/query_ivg_$mode.json" \
    >>"$OUT/query_$mode.log" 2>&1 || log "$mode queries rc=$?"
  push
  # a leg ends at the first query past the cap (snb_worker drops its connection): the rest run in further legs,
  # query_ivg_<mode>_rest<n>.json, until every query has a result (scripts.bench.alt.legs)
  for n in 1 2 3 4 5; do
    LEGS=$(ls "$OUT"/query_ivg_${mode}.json "$OUT"/query_ivg_${mode}_rest*.json 2>/dev/null)
    REST=$(PYTHONPATH=. $PY -m scripts.bench.alt.legs $LEGS)
    [[ -z "$REST" ]] && break
    log "$mode queries, leg $((n + 1)): $REST"
    PYTHONPATH=. IVG_NATIVE_CYPHER=$([[ $mode == native ]] && echo 1 || echo 0) IVG_IGNORE_NAMESPACE_CHECK=1 \
      $PY scripts/bench/alt/snb_worker.py query --ns "$NS" --root "$ROOT" --cap-s "$CAP" --only $REST \
      --out "$OUT/query_ivg_${mode}_rest$n.json" >>"$OUT/query_$mode.log" 2>&1 || log "$mode queries leg $((n + 1)) rc=$?"
    push
  done
done
log "DONE native $TAG"
