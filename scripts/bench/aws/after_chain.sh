#!/usr/bin/env bash
# Runs on the instance after the SF1/SF10 chain: waits for CHAIN-DONE, then
#   1. SF1, no index, both sides on the same statistics: the four plan-sensitive queries again  -> results/aws-sf1-aligned
#   2. SF1 with a (key, val) index on rdf_props on both sides (tuned, statistics aligned): every query and the updates -> results/aws-sf1-indexed
set -uo pipefail
cd /Users/tdyar/ws/iris-vector-graph
PY=.venv/bin/python; C="${IVG_BENCH_HOME:-$HOME/.cache/ivg-bench}"; ROOT="$C/snb1/out"; RES=docs/performance/alt-bench/results
log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a ~/logs/after.log; }
until grep -q CHAIN-DONE ~/logs/chain.log 2>/dev/null; do sleep 120; done
log "chain done; aligned re-run"
mkdir -p "$RES/aws-sf1-aligned" "$RES/aws-sf1-indexed"
$PY -m scripts.bench.alt.snb align-stats --prefix SNB1 --out "$RES/aws-sf1-aligned" >>"$RES/aws-sf1-aligned/align.log" 2>&1 || log "align rc=$?"
$PY -m scripts.bench.alt.snb query --prefix SNB1 --root "$ROOT" --runs 2 --reps 1 --cap-s 600 \
  --only BI03_popular_topics_in_country BI07_related_topics BI16_tag_window_posters BI_likes_total \
  --out "$RES/aws-sf1-aligned" >>"$RES/aws-sf1-aligned/query.log" 2>&1 || log "aligned query rc=$?"
log "index"
for ns in SNB1A SNB1B; do
  $PY - "$ns" >>"$RES/aws-sf1-indexed/index.log" 2>&1 <<'PY'
import sys, time, iris
from iris_vector_graph.schema import GraphSchema
ns = sys.argv[1]
c = iris.connect("localhost", 31972, ns, "_SYSTEM", "SYS"); cur = c.cursor()
t = time.time()
try:
    cur.execute('CREATE INDEX ivg_scratch_key_val ON Graph_KG.rdf_props ("key", val)'); c.commit()
except Exception as ex:  # side B got it early, for the quick read
    if "already" not in str(ex).lower() and "-324" not in str(ex):
        raise
    print(f"RESULT {ns} index already present")
built = time.time() - t
GraphSchema.tune_tables(cur); c.commit()
print(f"RESULT {ns} index built in {built:.1f}s, tuned")
PY
done
$PY -m scripts.bench.alt.snb align-stats --prefix SNB1 --out "$RES/aws-sf1-indexed" >>"$RES/aws-sf1-indexed/align.log" 2>&1 || log "align rc=$?"
log "indexed queries"
$PY -m scripts.bench.alt.snb query --prefix SNB1 --root "$ROOT" --runs 1 --reps 1 --cap-s 600 --out "$RES/aws-sf1-indexed" >>"$RES/aws-sf1-indexed/query.log" 2>&1 || log "indexed query rc=$?"
log "indexed updates"
$PY -m scripts.bench.alt.snb update --prefix SNB1 --root "$ROOT" --n 5 --runs 3 --id-offset 90000000 --seed 5000 --out "$RES/aws-sf1-indexed/updates" >>"$RES/aws-sf1-indexed/update.log" 2>&1 || log "indexed update rc=$?"
log "AFTER-DONE"
