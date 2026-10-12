#!/usr/bin/env bash
# One serial pass: for each dataset, for each engine in turn (never two at once), start it on the dataset's volume,
# load and verify if the volume is empty, run the traversal and algorithm suites, stop it. Then the in-process
# references (networkx, igraph) for the same dataset.
#
#   PY=/path/to/venv/bin/python WARMUP=3 RUNS=20 ./bench_all.sh OUTDIR [DATASET ...]
#
# Default datasets: S M L M193 NCM LDBC10 DRKG karate er500 er2000 btw500 btw2000
# Output: OUTDIR/<dataset>.jsonl (+ .meta.json), OUTDIR/<dataset>_algos.jsonl, OUTDIR/<dataset>.log
# Run it only when nothing else is loading the machine: it is a timing run.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PY:-python3}"
WARMUP="${WARMUP:-3}"
RUNS="${RUNS:-20}"
OUT="${1:?usage: bench_all.sh OUTDIR [DATASET ...]}"; shift
DATASETS=("$@")
[[ ${#DATASETS[@]} -gt 0 ]] || DATASETS=(S M L M193 NCM LDBC10 DRKG karate er500 er2000 btw500 btw2000)
mkdir -p "$OUT"
cd "$HERE"

for ds in "${DATASETS[@]}"; do
  for eng in neo4j memgraph; do
    echo "=== $ds / $eng" | tee -a "$OUT/$ds.log"
    ./competitors.sh start "$eng" "$ds" >>"$OUT/$ds.log" 2>&1 || { echo "start failed" | tee -a "$OUT/$ds.log"; continue; }
    if ! "$PY" load.py --engine "$eng" --dataset "$ds" --verify-only >>"$OUT/$ds.log" 2>&1; then
      "$PY" load.py --engine "$eng" --dataset "$ds" --out "$OUT/loads.jsonl" >>"$OUT/$ds.log" 2>&1 || { echo "load failed" | tee -a "$OUT/$ds.log"; ./competitors.sh stop "$eng" >/dev/null 2>&1; continue; }
    fi
    "$PY" run_queries.py --engine "$eng" --dataset "$ds" --warmup "$WARMUP" --runs "$RUNS" --out "$OUT/$ds.jsonl" 2>&1 | tee -a "$OUT/$ds.log"
    "$PY" run_algos.py --engine "$eng" --dataset "$ds" --warmup "$WARMUP" --runs "$RUNS" --out "$OUT/${ds}_algos.jsonl" 2>&1 | tee -a "$OUT/$ds.log"
    ./competitors.sh stop "$eng" >>"$OUT/$ds.log" 2>&1
  done
  "$PY" run_algos.py --engine networkx,igraph --dataset "$ds" --warmup "$WARMUP" --runs "$RUNS" --out "$OUT/${ds}_algos.jsonl" 2>&1 | tee -a "$OUT/$ds.log"
done
