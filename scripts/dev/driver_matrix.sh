#!/usr/bin/env bash
# usage: scripts/dev/driver_matrix.sh <outdir> [--tck <NS>]
#
# The driver drift gate (4.6.0 R230). Runs the E2E files in scripts/dev/driver_matrix_e2e.txt, one at a time, under
# two interpreters, and with --tck the whole TCK in namespace NS under each:
#   repo   IVG_PY_REPO   the repo's .venv (intersystems-irispython 5.4.0 when this was written)
#   fresh  IVG_PY_FRESH  a venv built from this checkout's pyproject.toml, as a fresh install resolves it (5.4.1);
#                        made at ~/.cache/ivg-bench/driver-matrix/venv-fresh with uv when it does not exist
# The two drivers decode a $DOUBLE stored in a VARCHAR column differently (docs/dev/driver-drift.md), so a green
# .venv run says nothing about what a user on a fresh install sees. Writes <outdir>/summary.txt: one line per file and
# interpreter (passed / failed counts), each interpreter's driver version, and with --tck the scenarios that pass under
# one driver and not the other (scripts/tck/summarize.py diff).
#
# The E2E files take the usual settings: IVG_TEST_CONTAINER, IVG_PORT, IVG_ARNO_PORT, IVG_NAMESPACE (a scratch
# namespace of your own; tests/conftest.py's default is USER). Deploy the namespace first; --tck NS needs
# scripts/tck/setup_namespace.sh NS. Never point it at a namespace another suite is using.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
if [ $# -lt 1 ]; then
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//' >&2
  exit 2
fi
out=$1; shift
tck_ns=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tck) tck_ns=${2:?--tck needs a namespace}; shift 2 ;;
    *) echo "driver_matrix.sh: unknown argument $1" >&2; exit 2 ;;
  esac
done
mkdir -p "$out"; out=$(cd "$out" && pwd)

# the repo's .venv: this checkout's, else (a git worktree) the main checkout's
py_repo=${IVG_PY_REPO:-$repo/.venv/bin/python}
if [ ! -x "$py_repo" ] && [ -z "${IVG_PY_REPO:-}" ]; then
  py_repo=$(cd "$(git -C "$repo" rev-parse --git-common-dir)/.." && pwd)/.venv/bin/python
fi
fresh_dir=$HOME/.cache/ivg-bench/driver-matrix/venv-fresh
py_fresh=${IVG_PY_FRESH:-$fresh_dir/bin/python}
if [ ! -x "$py_fresh" ] && [ -z "${IVG_PY_FRESH:-}" ]; then
  echo "building $fresh_dir from $repo/pyproject.toml (what a fresh install resolves)" >&2
  uv venv -q --python 3.11 "$fresh_dir" && (cd "$repo" && VIRTUAL_ENV="$fresh_dir" uv pip install -q -e ".[dev,full]") \
    || { echo "driver_matrix.sh: could not build $fresh_dir" >&2; exit 2; }
fi

driver() { "$1" -c 'import importlib.metadata as m; print(m.version("intersystems-irispython"))' 2>/dev/null || echo "?"; }

summary=$out/summary.txt
: >"$summary"
status=0
for tag in repo fresh; do
  if [ "$tag" = repo ]; then py=$py_repo; else py=$py_fresh; fi
  echo "$tag $py intersystems-irispython $(driver "$py")" | tee -a "$summary"
  while IFS= read -r rel; do
    case "$rel" in ''|'#'*) continue ;; esac
    log=$out/$tag/$(basename "$rel" .py).log
    mkdir -p "$out/$tag"
    (cd "$repo" && PYTHONPATH="$repo" "$py" -m pytest -p no:cacheprovider -q "$rel") >"$log" 2>&1
    rc=$?
    [ $rc -eq 0 ] || status=1
    echo "  $tag $rel: $(tail -1 "$log" | sed 's/=//g; s/^ *//; s/ *$//')" | tee -a "$summary"
  done <"$here/driver_matrix_e2e.txt"
  if [ -n "$tck_ns" ]; then
    PYTHON=$py IVG_REPO=$repo IVG_TCK_NAMESPACE=$tck_ns "$repo/scripts/tck/run_all.sh" "$out/tck_$tag" \
      >"$out/tck_$tag.log" 2>&1 || status=1
    echo "  $tag TCK ($tck_ns): $(grep -E 'eligible|failed' "$out/tck_$tag.log" | tail -2 | tr '\n' ' ')" | tee -a "$summary"
  fi
done
if [ -n "$tck_ns" ]; then
  echo "TCK scenarios that pass under one driver only:" | tee -a "$summary"
  "$py_repo" "$repo/scripts/tck/summarize.py" diff "$out/tck_repo/results.tsv" "$out/tck_fresh/results.tsv" \
    | tee -a "$summary"
fi
exit $status
