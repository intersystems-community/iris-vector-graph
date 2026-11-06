#!/usr/bin/env bash
# usage: scripts/dev/driver_matrix.sh <outdir> [--tck <NS>] [--no-unit]
#
# The driver drift gate (4.6.0 R230, R309). Under each driver of DRIVERS, with that driver pinned: the unit suite
# (unless --no-unit), then the E2E files in scripts/dev/driver_matrix_e2e.txt one at a time, and with --tck the whole
# TCK in namespace NS. Two interpreters:
#   repo   IVG_PY_REPO   intersystems-irispython 5.4.0 (the repo's .venv has it)
#   fresh  IVG_PY_FRESH  intersystems-irispython 5.4.1 (what a fresh install resolves)
# By default each is a venv built from this checkout's pyproject.toml with its driver pinned, at
# ~/.cache/ivg-bench/driver-matrix/venv-<driver>, made with uv when it does not exist. An interpreter whose driver is
# not the one its slot names is refused. The two drivers decode a $DOUBLE stored in a VARCHAR column differently
# (docs/dev/driver-drift.md), so a green run under one says nothing about the other. Writes <outdir>/summary.txt: one
# line per suite and interpreter (passed / failed counts), each interpreter's driver version, and with --tck the
# scenarios that pass under one driver and not the other (scripts/tck/summarize.py diff).
#
# The E2E files take the usual settings: IVG_TEST_CONTAINER, IVG_PORT, IVG_ARNO_PORT, IVG_NAMESPACE (a scratch
# namespace of your own; tests/conftest.py's default is USER). Deploy the namespace first; --tck NS needs
# scripts/tck/setup_namespace.sh NS. Never point it at a namespace another suite is using. scripts/ci-parity.sh runs
# this with --no-unit (it has run the unit suite under both drivers itself) in a namespace it makes for the run.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../.." && pwd)
DRIVERS=(5.4.0 5.4.1)
if [ $# -lt 1 ]; then
  sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//' >&2
  exit 2
fi
out=$1; shift
tck_ns=""
unit=1
while [ $# -gt 0 ]; do
  case "$1" in
    --tck) tck_ns=${2:?--tck needs a namespace}; shift 2 ;;
    --no-unit) unit=0; shift ;;
    *) echo "driver_matrix.sh: unknown argument $1" >&2; exit 2 ;;
  esac
done
mkdir -p "$out"; out=$(cd "$out" && pwd)

driver() { "$1" -c 'import importlib.metadata as m; print(m.version("intersystems-irispython"))' 2>/dev/null || echo "?"; }
# a pytest log's last line, its "=" rule stripped
outcome() { tail -1 "$1" | sed 's/=//g; s/^ *//; s/ *$//'; }

# the interpreter for driver $1: the slot's override, else a venv with that driver pinned (built once)
interpreter() {
  local drv=$1 override=$2 dir
  if [ -n "$override" ]; then echo "$override"; return; fi
  dir=$HOME/.cache/ivg-bench/driver-matrix/venv-$drv
  if [ ! -x "$dir/bin/python" ]; then
    echo "building $dir from $repo/pyproject.toml with intersystems-irispython==${drv}" >&2
    uv venv -q --python 3.11 "$dir" && (cd "$repo" && VIRTUAL_ENV="$dir" uv pip install -q -e ".[dev,full]") \
      && VIRTUAL_ENV="$dir" uv pip install -q "intersystems-irispython==${drv}" \
      || { echo "driver_matrix.sh: could not build $dir" >&2; return 1; }
  fi
  echo "$dir/bin/python"
}

py_repo=$(interpreter "${DRIVERS[0]}" "${IVG_PY_REPO:-}") || exit 2
py_fresh=$(interpreter "${DRIVERS[1]}" "${IVG_PY_FRESH:-}") || exit 2

summary=$out/summary.txt
: >"$summary"
status=0
for tag in repo fresh; do
  if [ "$tag" = repo ]; then py=$py_repo; drv=${DRIVERS[0]}; else py=$py_fresh; drv=${DRIVERS[1]}; fi
  echo "$tag $py intersystems-irispython $(driver "$py")" | tee -a "$summary"
  if [ "$(driver "$py")" != "$drv" ]; then
    echo "  $tag: $py has intersystems-irispython $(driver "$py"), not $drv: refused" | tee -a "$summary"
    status=1
    continue
  fi
  mkdir -p "$out/$tag"
  if [ "$unit" = 1 ]; then
    log=$out/$tag/unit.log
    (cd "$repo" && SKIP_IRIS_TESTS=true DOCKER_HOST=unix:///nonexistent/driver-matrix.sock PYTHONPATH="$repo" \
      "$py" -m pytest -p no:cacheprovider -q tests/unit) >"$log" 2>&1 || status=1
    echo "  $tag tests/unit: $(outcome "$log")" | tee -a "$summary"
  fi
  while IFS= read -r rel; do
    case "$rel" in ''|'#'*) continue ;; esac
    log=$out/$tag/$(basename "$rel" .py).log
    (cd "$repo" && PYTHONPATH="$repo" "$py" -m pytest -p no:cacheprovider -q "$rel") >"$log" 2>&1 || status=1
    echo "  $tag $rel: $(outcome "$log")" | tee -a "$summary"
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
