#!/usr/bin/env bash
# Run the unit suite the way CI runs it, before pushing.
#
# CI checks out only what git ships. Tests that read a gitignored file (specs/ is
# ignored), an installed-but-undeclared extra, or a local .venv quirk pass here and
# fail there — main was red for eight pushes in a row that way (DEBT entry 10). This
# copies exactly the files a commit of the working tree would carry (tracked plus
# untracked-and-not-ignored) into a scratch directory, builds a fresh venv from
# pyproject.toml, and runs:
#
#   1. the unit suite with SKIP_IRIS_TESTS=true, warnings as errors (pyproject's
#      filterwarnings), no --disable-warnings;
#   2. a wheel build, installed into a second clean venv, imported with -W error,
#      and checked for the ObjectScript classes it must carry.
#
# Usage: scripts/ci-parity.sh [python-version ...]   (default: 3.12 3.13)
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
UV="${UV:-$(command -v uv || echo "$HOME/.local/bin/uv")}"
VERSIONS=("$@")
[ ${#VERSIONS[@]} -eq 0 ] && VERSIONS=(3.12 3.13)

WORK="$(mktemp -d "${TMPDIR:-/tmp}/ivg-ci-parity.XXXXXX")"
echo "ci-parity: scratch copy in $WORK"

cd "$REPO"
git ls-files -co --exclude-standard -z | while IFS= read -r -d '' f; do
    [ -e "$f" ] || continue # deleted in the working tree, not yet staged
    [ -d "$f" ] && continue # a submodule: actions/checkout does not fetch them
    mkdir -p "$WORK/src/$(dirname "$f")"
    cp -p "$f" "$WORK/src/$f"
done

status=0
for py in "${VERSIONS[@]}"; do
    echo "=== unit suite, Python $py ==="
    venv="$WORK/venv-$py"
    "$UV" venv -q --python "$py" "$venv"
    (cd "$WORK/src" && VIRTUAL_ENV="$venv" "$UV" pip install -q -e ".[dev,full]")
    # Some fixtures (arno_iris_connection) skip only when `docker ps` does not list
    # the container, so a running local container lets live tests run here that CI
    # skips. CI's runner has no Docker daemon: neither does this run. The container
    # names stay at their defaults, which tests assert on.
    if ! (cd "$WORK/src" && SKIP_IRIS_TESTS=true DOCKER_HOST=unix:///nonexistent/ci-parity.sock \
        "$venv/bin/python" -m pytest tests/unit \
        -q --no-header --tb=short -p no:cacheprovider -rfE); then
        status=1
    fi
done

echo "=== wheel ==="
(cd "$WORK/src" && "$UV" build -q --wheel --out-dir "$WORK/dist")
wheel="$(ls "$WORK"/dist/*.whl)"
"$UV" venv -q --python "${VERSIONS[0]}" "$WORK/venv-wheel"
VIRTUAL_ENV="$WORK/venv-wheel" "$UV" pip install -q "$wheel"
if ! (cd "$WORK" && "$WORK/venv-wheel/bin/python" -W error - <<'EOF'); then
import iris_vector_graph
from pathlib import Path
from iris_vector_graph._engine.class_deploy import packaged_class_dir

d = packaged_class_dir()
assert d is not None, "the wheel carries no ObjectScript classes"
assert "site-packages" in str(d), f"classes found outside the install: {d}"
n = len(list((Path(d) / "Graph" / "KG").glob("*.cls")))
assert n > 10, f"only {n} Graph.KG classes in the wheel"
print(f"wheel OK: {n} Graph.KG classes under {d}")
EOF
    status=1
fi

if [ "$status" -eq 0 ]; then
    echo "ci-parity: PASS (scratch at $WORK)"
else
    echo "ci-parity: FAIL (scratch kept at $WORK)"
fi
exit "$status"
