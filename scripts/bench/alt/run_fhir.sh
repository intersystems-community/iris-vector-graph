#!/usr/bin/env bash
# Spec 235 FHIR benchmark, side A then side B, fixture and scale sets, two rounds. Each side's ObjectScript is deployed into
# IVGFHIR first (the script imports the side's Python itself via --repo). Run from the repository root on a quiet machine.
set -u
PY="$(pwd)/.venv/bin/python"
ROOT="$(pwd)"
LOG=/tmp/altrun/fhir
OUT="$ROOT/docs/performance/alt-bench/results/fhir-1"
mkdir -p "$LOG" "$OUT"
for round in 1 2; do
  for set_ in fixture scale; do
    for side in A B; do
      tree=/tmp/ivg-src-$side
      # Both sides are 4.2.x and have group-by PPR and the reports; `baseline` would skip them on A and leave nothing to compare.
      label=current
      echo "=== $(date +%H:%M:%S) $set_ $side round $round" | tee -a "$LOG/queue.log"
      ( cd "$tree" && "$PY" -c 'from tests.e2e.fhir_conftest import connect, deploy; c=connect(); e=deploy(c); print("deploy errors:", len(e))' \
        && "$PY" -m scripts.fhir.bench_235 --label "$label" --repo "$tree" --set "$set_" --out "$OUT/fhir_${set_}_${side}_$round.json" ) \
        > "$LOG/${set_}_${side}_r$round.log" 2>&1
      echo "    exit=$?" | tee -a "$LOG/queue.log"
    done
  done
done
