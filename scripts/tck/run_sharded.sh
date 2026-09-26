#!/usr/bin/env bash
# Run the whole TCK split across several IRIS namespaces in parallel.
#
# usage: scripts/tck/run_sharded.sh <outdir> <NS> [NS ...]
#   Areas are dealt to namespaces largest-first (by Scenario count) so the
#   shards finish together. Each shard is one run_all.sh, which takes that
#   namespace's lock. Deploy each namespace first (setup_namespace.sh).
#   Same environment variables as run_all.sh (IVG_REPO, IVG_TCK_MULTIGRAPH, ...).
#
# Writes <outdir>/<area>/TESTS-*.xml and <outdir>/results.tsv.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "${IVG_REPO:-$here/../..}" && pwd)
[ $# -ge 2 ] || { sed -n '2,10p' "$0" >&2; exit 2; }
out=$1; shift
mkdir -p "$out"; out=$(cd "$out" && pwd)
py=${PYTHON:-$repo/.venv/bin/python}
if [ ! -x "$py" ] && [ -z "${PYTHON:-}" ]; then
  py=$(cd "$(git -C "$repo" rev-parse --git-common-dir)/.." && pwd)/.venv/bin/python
fi

queues=$("$py" - "$repo/tests/tck/features" "$#" <<'PY'
import sys
from pathlib import Path
feat, n = Path(sys.argv[1]), int(sys.argv[2])
areas = sorted(
    ((sum(f.read_text().count("Scenario") for f in d.glob("*.feature")), d.name)
     for d in feat.glob("*/*/") if d.is_dir()),
    reverse=True,
)
load, q = [0] * n, [[] for _ in range(n)]
for w, a in areas:
    i = load.index(min(load))
    load[i] += w
    q[i].append(a)
print("\n".join(" ".join(x) for x in q))
PY
)

i=0; pids=()
while IFS= read -r line; do
  ns=${!#}; j=0
  for n in "$@"; do [ $j = $i ] && ns=$n; j=$((j + 1)); done
  # shellcheck disable=SC2086
  IVG_REPO=$repo IVG_TCK_NAMESPACE=$ns "$here/run_all.sh" "$out" $line >"$out/shard_$ns.log" 2>&1 &
  pids+=($!); i=$((i + 1))
done <<<"$queues"
for p in "${pids[@]}"; do wait "$p" || true; done

"$py" "$here/summarize.py" "$out" "$out/results.tsv" --features "$repo/tests/tck/features"
