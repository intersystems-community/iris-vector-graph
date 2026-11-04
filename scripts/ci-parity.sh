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
#   2. the doc consistency test (tests/docs/test_docs_consistent.py), Docker hidden;
#   3. when the test container is running: the doc-run test (tests/docs/test_docs_run.py,
#      namespace IVG460DOC) and the installed-inventory test (tests/e2e/test_262_inventory.py);
#   4. a wheel build, installed into a second clean venv, imported with -W error,
#      and checked for the ObjectScript classes it must carry.
#
# tests/unit/test_ci_parity_gates.py fails if any of these gates is dropped.
#
# CI stays library-free. The Arno reader gate (scripts/arno_reader_gate.py --check and
# tests/e2e/test_236_reader_gate.py, run with IVG_REQUIRE_ARNO=1) belongs to the
# enterprise tier and is a PRE_RELEASE_CHECKLIST.md step, not part of this script.
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
    if [ -L "$f" ]; then # a tracked symlink: checkout keeps it, broken or not (tests/tck/features)
        mkdir -p "$WORK/src/$(dirname "$f")"
        cp -pP "$f" "$WORK/src/$f"
        continue
    fi
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

venv0="$WORK/venv-${VERSIONS[0]}"

# Doc-test layer 2 (4.6.0 R043): every link, path, class, method, global, env var, port and script the docs name
# exists. No container: it always runs, with the Docker daemon hidden as above. Its known failures are XFAIL
# (tests/docs/consistency_known_failures.txt); -rx lists them.
echo "=== docs agree with the code (R043) ==="
if ! (cd "$WORK/src" && SKIP_IRIS_TESTS=true DOCKER_HOST=unix:///nonexistent/ci-parity.sock \
    "$venv0/bin/python" -m pytest tests/docs/test_docs_consistent.py \
    -q --no-header --tb=short -p no:cacheprovider -rfEx); then
    status=1
fi

# Doc-test layer 1 (R042) and the installed inventory (spec 262 T061) need the test container. They run when it is
# running, and say so when it is not; neither is ever skipped silently.
CONTAINER="${IVG_TEST_CONTAINER:-ivg-iris-enterprise}"
if [ "$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null)" = "running" ]; then
    echo "=== docs run and their answers match (R042), $CONTAINER namespace IVG460DOC ==="
    if ! (cd "$WORK/src" && IVG_NAMESPACE=IVG460DOC "$venv0/bin/python" -m pytest tests/docs/test_docs_run.py \
        -q --no-header --tb=line -p no:cacheprovider -rfExs); then
        status=1
    fi
    echo "=== installed inventory (spec 262 T061) ==="
    if [ -f "$WORK/src/tests/e2e/test_262_inventory.py" ]; then
        if ! (cd "$WORK/src" && "$venv0/bin/python" -m pytest tests/e2e/test_262_inventory.py \
            -q --no-header --tb=short -p no:cacheprovider -rfEs); then
            status=1
        fi
    else
        echo "ci-parity: PENDING T061: tests/e2e/test_262_inventory.py is not written yet; inventory gate not run"
    fi
else
    echo "ci-parity: $CONTAINER is not running: SKIPPED doc-test layer 1 (R042) and the inventory gate (T061)"
fi

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
# spec 262 T057 (FR-015) and R080: benchmark code, the Xecute helper and GraphOperators stay in iris_src/dev
retired = ["Graph/KG/Benchmark.cls", "Graph/KG/BenchFormat.cls", "User.Exec.cls", "iris/vector/graph/GraphOperators.cls"]
shipped = [r for r in retired if (Path(d) / r).exists()]
assert not shipped, f"the wheel ships development-only classes: {shipped}"
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
