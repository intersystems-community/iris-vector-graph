#!/usr/bin/env bash
# Run the vendored openCypher TCK, one behave process per area, JUnit XML out.
#
# usage: scripts/tck/run_all.sh <outdir> [area ...]
#   no areas -> every tests/tck/features/<category>/<area>/ directory
#
# environment (all optional):
#   IVG_REPO            repo to run from (default: this script's repo)
#   IVG_TCK_NAMESPACE   IRIS namespace the harness connects to (default USER)
#   IVG_TEST_CONTAINER  IRIS container (default ivg-iris-enterprise)
#   IVG_PORT            host superserver port (default 31972)
#   IVG_TCK_MULTIGRAPH  0 = run with multigraph mode off (default on)
#   IVG_TCK_RUN_IGNORED 1 = also run scenarios tagged @ignore upstream
#   PYTHON              interpreter with behave + the repo's deps (default $IVG_REPO/.venv/bin/python)
#   TCK_TIMEOUT         seconds per area (default 900; needs `timeout` on PATH)
#   TCK_LOCK            1 (default) = hold /tmp/ivg_iris_<NS>.lock (USER: /tmp/ivg_iris.lock)
#                       for the whole run; 0 = caller holds it. Do not hold the lock
#                       yourself and leave TCK_LOCK=1: the run would wait on itself.
#
# Writes <outdir>/<area>/TESTS-*.xml and <outdir>/<area>.log, and prints the
# summary from scripts/tck/summarize.py at the end.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "${IVG_REPO:-$here/../..}" && pwd)
[ $# -ge 1 ] || { sed -n '2,20p' "$0" >&2; exit 2; }
out=$1; shift
mkdir -p "$out"; out=$(cd "$out" && pwd)

ns=${IVG_TCK_NAMESPACE:-}
if [ -z "$ns" ] || [ "$(printf %s "$ns" | tr '[:lower:]' '[:upper:]')" = USER ]; then lock=/tmp/ivg_iris.lock; else lock=/tmp/ivg_iris_$ns.lock; fi
py=${PYTHON:-$repo/.venv/bin/python}
if [ ! -x "$py" ] && [ -z "${PYTHON:-}" ]; then  # a git worktree: use the main checkout's venv
  py=$(cd "$(git -C "$repo" rev-parse --git-common-dir)/.." && pwd)/.venv/bin/python
fi
[ -x "$py" ] || { echo "run_all.sh: no python at $py (set PYTHON)" >&2; exit 2; }

if [ "${TCK_LOCK:-1}" = 1 ] && [ -z "${_TCK_LOCKED:-}" ]; then
  # Re-exec under an exclusive flock (python: flock(1) is not on macOS).
  exec /usr/bin/env python3 -c 'import fcntl,os,sys;l=open(sys.argv[1],"w");fcntl.flock(l,fcntl.LOCK_EX);os.environ["_TCK_LOCKED"]="1";os.execv(sys.argv[2],sys.argv[2:])' \
    "$lock" "$0" "$out" "$@"
fi

export IVG_TEST_CONTAINER=${IVG_TEST_CONTAINER:-ivg-iris-enterprise}
export IVG_PORT=${IVG_PORT:-31972}
export PYTHONPATH=$repo

if command -v timeout >/dev/null 2>&1; then to=(timeout "${TCK_TIMEOUT:-900}"); else to=(); fi

cd "$repo"
dirs=()
if [ $# -eq 0 ]; then
  for d in tests/tck/features/*/*/; do dirs+=("$d"); done
else
  for a in "$@"; do
    m=(tests/tck/features/*/"$a"/)
    [ -d "${m[0]}" ] || { echo "run_all.sh: no area $a" >&2; exit 2; }
    dirs+=("${m[0]}")
  done
fi

{
  echo "repo       $repo @ $(git -C "$repo" rev-parse --short HEAD)$(git -C "$repo" diff --quiet HEAD -- . 2>/dev/null || echo ' (dirty)')"
  echo "namespace  ${ns:-USER}  container $IVG_TEST_CONTAINER:$IVG_PORT"
  echo "multigraph $([ "${IVG_TCK_MULTIGRAPH:-1}" = 0 ] && echo off || echo on)  run-ignored ${IVG_TCK_RUN_IGNORED:-0}"
  echo "started    $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} | tee "$out/RUN_INFO"

for d in "${dirs[@]}"; do
  a=$(basename "$d")
  rm -f "$out/$a"/TESTS-*.xml 2>/dev/null || true
  ${to[@]+"${to[@]}"} "$py" -m behave -f null --junit --junit-directory "$out/$a" "$d" >"$out/$a.log" 2>&1 || true
  echo "$a done"
done
echo "finished   $(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$out/RUN_INFO"

"$py" "$here/summarize.py" "$out" "$out/results.tsv" --features "$repo/tests/tck/features"
