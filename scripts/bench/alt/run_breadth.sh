#!/usr/bin/env bash
# The remaining breadth, one step after another on a quiet machine. A failing step is logged and the queue goes on.
#   scripts/bench/alt/run_breadth.sh  (from the repository root)
set -u
PY="$(pwd)/.venv/bin/python"   # absolute: some steps change directory into a side tree
LOG=/tmp/altrun/breadth
mkdir -p "$LOG"
step() { name="$1"; shift; echo "=== $(date +%H:%M:%S) $name" | tee -a "$LOG/queue.log"; "$@" > "$LOG/$name.log" 2>&1; echo "    exit=$?" | tee -a "$LOG/queue.log"; }

step 00-setup      $PY scripts/bench/alt/setup_sides.py all --alt-ref alt-parity
step 01-migration  $PY -m scripts.bench.alt.run_migration --run-id migration-1 --reps 3
step 02-index      $PY -u scripts/bench/alt/run_matrix.py --reps 3 --run-id breadth-index --max-wait 900 --alt-ref alt-parity --suites index-maintenance-20k index-maintenance-100k
step 03-bidir      $PY -u scripts/bench/alt/run_matrix.py --reps 5 --run-id breadth-bidir --max-wait 900 --alt-ref alt-parity --suites integration-bidirectional-ppr-perf
for round in 1 2; do
  for set_ in fixture scale; do
    for side in A B; do
      tree=/tmp/ivg-src-$side
      ns=IVGFHIR
      label=baseline; [ $side = B ] && label=current
      out=docs/performance/alt-bench/results/fhir-1
      mkdir -p $out
      step 04-fhir-$set_-$side-r$round bash -c "cd $tree && $PY -c 'from tests.e2e.fhir_conftest import connect, deploy; c=connect(); e=deploy(c); print(\"deploy errors:\", len(e))' && $PY -m scripts.fhir.bench_235 --label $label --repo $tree --set $set_ --out /Users/tdyar/ws/iris-vector-graph/$out/fhir_${set_}_${side}_$round.json"
    done
  done
done
step 05-drkg-full $PY -u scripts/bench/alt/run_matrix.py --reps 1 --run-id breadth-drkg-full --max-wait 900 --alt-ref alt-parity --suites drkg-load-full
step 06-q1q6-l    $PY -u scripts/bench/alt/run_matrix.py --reps 5 --run-id breadth-q1q6-l --max-wait 900 --alt-ref alt-parity --suites bench-q1q6-l
echo "=== done $(date +%H:%M:%S)" | tee -a "$LOG/queue.log"
