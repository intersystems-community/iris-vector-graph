#!/usr/bin/env bash
# SF10 on all three engines, one instance: IVG (this checkout, native Cypher, the default) in one namespace, then Neo4j and
# Memgraph, one engine at a time, every runner drawing its parameter sets from the same data with the same seed.
# Results under results/aws-<tag>, pushed to S3 after every step (pull.sh on the laptop is the second copy).
# Usage: [SCALE=sf10|sf1] [CAP=900] [NEO4J_HEAP=16g NEO4J_PAGECACHE=48g MEMGRAPH_MEMORY_MIB=131072] run_sf10_compare.sh <tag>
#   run_sf10_compare.sh sf10-3          SCALE=sf1 run_sf10_compare.sh sf1-4
# The 2026-10-21 SF10 run's Memgraph load failed under its 32 GB limit (r7g.4xlarge, 128 GB); on a 256 GB instance the
# limits above give IRIS its default 25% of memory for buffers (64 GB), Neo4j 64 GB (heap + page cache), Memgraph 128 GB.
set -uo pipefail
TAG="$1"; CAP="${CAP:-900}"; C="${IVG_BENCH_HOME:-$HOME/.cache/ivg-bench}"
case "${SCALE:-sf10}" in
  sf10) PREFIX=SNB10; NS=SNB10B; CDS=SNB10; ROOT="$C/snb10/out" ;;
  sf1) PREFIX=SNB1; NS=SNB1B; CDS=SNB1; ROOT="$C/snb1/out" ;;
  *) echo "SCALE must be sf10 or sf1" >&2; exit 2 ;;
esac
cd /Users/tdyar/ws/iris-vector-graph
PY=.venv/bin/python; CPY="$C/comp-venv/bin/python"
OUT="docs/performance/alt-bench/results/aws-$TAG"; mkdir -p "$OUT"
git rev-parse --short HEAD >"$OUT/IVG_COMMIT"
export NEO4J_HEAP="${NEO4J_HEAP:-16g}" NEO4J_PAGECACHE="${NEO4J_PAGECACHE:-48g}" MEMGRAPH_MEMORY_MIB="${MEMGRAPH_MEMORY_MIB:-131072}"
log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$OUT/run.log"; }
push() { [[ -n "${BUCKET:-}" ]] && { aws s3 sync "$OUT" "s3://$BUCKET/results/aws-$TAG" --only-show-errors || log "s3 push failed (rc=$?)"; }; return 0; }

log "setup $NS"; $PY -m scripts.bench.alt.snb setup --prefix "$PREFIX" --side B --out "$OUT" >>"$OUT/setup.log" 2>&1 || log "setup rc=$?"; push
log "bulk load $NS"
$PY scripts/bench/alt/snb_bulk.py load --side B --ns "$NS" --root "$ROOT" --files "$C/snb_bulk_files_$TAG" --out "$OUT/bulk_load_B.json" \
  --skip-ifind --index-jobs "${INDEX_JOBS:-8}" >>"$OUT/load.log" 2>&1 || log "bulk load rc=$?"; push
log "weights"; $PY -m scripts.bench.alt.snb weights --prefix "$PREFIX" --side B --root "$ROOT" --out "$OUT" >>"$OUT/weights.log" 2>&1 || log "weights rc=$?"; push
log "ivg native"; MODES=native bash scripts/bench/aws/run_native.sh "$TAG" "$NS" "$ROOT" "$CAP" >>"$OUT/native.log" 2>&1 || log "native rc=$?"
cp -r "docs/performance/alt-bench/results/aws-native-$TAG/." "$OUT/" 2>/dev/null; push
for eng in neo4j memgraph; do
  log "$eng start"; bash scripts/bench/competitors/competitors.sh start $eng "$CDS" >>"$OUT/comp.log" 2>&1; sleep 30
  log "$eng load"; $CPY scripts/bench/competitors/snb.py load --engine $eng --root "$ROOT" --out "$OUT/${eng}_load.jsonl" >>"$OUT/comp.log" 2>&1 || log "$eng load rc=$?"; push
  log "$eng weights"; $CPY scripts/bench/competitors/snb.py weights --engine $eng --root "$ROOT" >>"$OUT/comp.log" 2>&1 || log "$eng weights rc=$?"; push
  log "$eng queries"; $CPY scripts/bench/competitors/snb.py query --engine $eng --root "$ROOT" --warmup 1 --reps 3 --timeout-s "$CAP" --out "$OUT/${eng}_snb.jsonl" >>"$OUT/comp.log" 2>&1 || log "$eng query rc=$?"; push
  bash scripts/bench/competitors/competitors.sh stop $eng >>"$OUT/comp.log" 2>&1
done
push; log "DONE $TAG"
