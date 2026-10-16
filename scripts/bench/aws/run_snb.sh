#!/usr/bin/env bash
# One SNB scale on the run instance: IVG sides A/B, then Neo4j and Memgraph, results under results/aws-<tag>.
# Usage: run_snb.sh <tag> <prefix> <data-root> <competitor-dataset> <loader: engine|bulk> [cap-s] [runs] [reps]
#   run_snb.sh sf01 SNBCLEAN ~/.cache/ivg-bench/snb01/out SNB01 engine
#   run_snb.sh sf1  SNB1     ~/.cache/ivg-bench/snb1/out  SNB1  bulk 600 1 1   (UPD_N=5, SKIP_UPDATES=1 env)
set -uo pipefail
TAG="$1"; PREFIX="$2"; ROOT="$3"; CDS="$4"; LOADER="$5"; CAP="${6:-120}"; RUNS="${7:-3}"; REPS="${8:-3}"
cd /Users/tdyar/ws/iris-vector-graph
PY=.venv/bin/python; CPY="${IVG_BENCH_HOME:-$HOME/.cache/ivg-bench}/comp-venv/bin/python"; UPD_N="${UPD_N:-40}"
OUT="docs/performance/alt-bench/results/aws-$TAG"; mkdir -p "$OUT"
# A per-scale ceiling on the query plan (n_params,warmup,reps), read from a file so it can be set for a scale already queued.
CAPF="${IVG_BENCH_HOME:-$HOME/.cache/ivg-bench}/plan_cap_$TAG"; [[ -f "$CAPF" ]] && export IVG_SNB_PLAN_CAP="$(cat "$CAPF")"
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$OUT/run.log"; }

log "setup $PREFIX"; $PY -m scripts.bench.alt.snb setup --prefix "$PREFIX" --out "$OUT" >>"$OUT/setup.log" 2>&1 || log "setup rc=$? (see setup.log)"
if [[ "$LOADER" == bulk ]]; then
  log "bulk load"; for s in A B; do $PY scripts/bench/alt/snb_bulk.py load --side $s --ns "${PREFIX}$s" --root "$ROOT" --files "${IVG_BENCH_HOME:-$HOME/.cache/ivg-bench}/snb_bulk_files_$TAG" --out "$OUT/bulk_load_$s.json" --skip-ifind >>"$OUT/load.log" 2>&1 || log "bulk load $s rc=$?"; done
else
  log "engine load"; $PY -m scripts.bench.alt.snb load --prefix "$PREFIX" --root "$ROOT" --out "$OUT" >>"$OUT/load.log" 2>&1 || log "load rc=$?"
fi
log "weights"; $PY -m scripts.bench.alt.snb weights --prefix "$PREFIX" --root "$ROOT" --out "$OUT" >>"$OUT/weights.log" 2>&1 || log "weights rc=$?"
log "ivg queries"; $PY -m scripts.bench.alt.snb query --prefix "$PREFIX" --root "$ROOT" --runs "$RUNS" --reps "$REPS" --cap-s "$CAP" --out "$OUT" >>"$OUT/query.log" 2>&1 || log "query rc=$?"
if [[ "${SKIP_UPDATES:-0}" != 1 ]]; then
  log "ivg updates"; $PY -m scripts.bench.alt.snb update --prefix "$PREFIX" --root "$ROOT" --n "$UPD_N" --runs 3 --id-offset 80000000 --seed 3000 --out "$OUT/updates" >>"$OUT/update.log" 2>&1 || log "update rc=$?"
fi
for eng in neo4j memgraph; do
  log "$eng start"; bash scripts/bench/competitors/competitors.sh start $eng "$CDS" >>"$OUT/comp.log" 2>&1; sleep 30
  log "$eng load"; $CPY scripts/bench/competitors/snb.py load --engine $eng --root "$ROOT" --out "$OUT/${eng}_load.jsonl" >>"$OUT/comp.log" 2>&1 || log "$eng load rc=$?"
  log "$eng weights"; $CPY scripts/bench/competitors/snb.py weights --engine $eng --root "$ROOT" >>"$OUT/comp.log" 2>&1 || log "$eng weights rc=$?"
  log "$eng queries"; $CPY scripts/bench/competitors/snb.py query --engine $eng --root "$ROOT" --warmup 1 --reps 3 --timeout-s "$CAP" --out "$OUT/${eng}_snb.jsonl" >>"$OUT/comp.log" 2>&1 || log "$eng query rc=$?"
  if [[ "${SKIP_UPDATES:-0}" != 1 ]]; then
    for k in 0 1 2; do $CPY scripts/bench/competitors/snb.py update --engine $eng --root "$ROOT" --n "$UPD_N" --seed $((3000+k)) --id-offset $((80000000+k*1000000)) --out "$OUT/updates/${eng}_updates.jsonl" >>"$OUT/comp.log" 2>&1 || log "$eng update rc=$?"; done
  fi
  bash scripts/bench/competitors/competitors.sh stop $eng >>"$OUT/comp.log" 2>&1
done
log "DONE $TAG"
