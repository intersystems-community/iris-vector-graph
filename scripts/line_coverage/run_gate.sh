#!/usr/bin/env bash
# Produce the line-monitor dumps the ObjectScript coverage gate reads, then check them.
#
#   scripts/line_coverage/run_gate.sh [OUTDIR]        # run everything (hours), then check
#   scripts/line_coverage/run_gate.sh --list          # print the chunks and exit
#   CHUNK=10 scripts/line_coverage/run_gate.sh        # files per container restart (default 25)
#
# Why chunks and restarts: IRIS only counts lines in jobs that start after the monitor starts, and a
# test session that recompiles a class stops it counting that routine in jobs already running. A
# restart per chunk keeps every dump cold; the dumps are merged as a union (linecov.py).
#
# Three groups, each monitored in the namespace where its tests run:
#   IVG236   tests using the scratch namespace (ivg_236_scratch / IVG236)
#   IVGFHIR  tests using the FHIR fixtures (fhir_conftest / IVGFHIR)
#   USER     every other test, plus the FHIR tests again (some connect to USER)
# Needs the enterprise container (scripts/enterprise-container.sh up). Never run it beside another
# suite on the same container.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
CONTAINER="${IVG_TEST_CONTAINER:-ivg-iris-enterprise}"
export IVG_TEST_CONTAINER="$CONTAINER" IVG_PORT="${IVG_PORT:-31972}"
CHUNK="${CHUNK:-25}"
cd "$REPO"

ALL="$(ls tests/integration/test_*.py tests/e2e/test_*.py)"
SCRATCH="$(grep -lE 'ivg_236_scratch|IVG236' $ALL || true)"
FHIR="$(grep -lE 'fhir_conftest|IVGFHIR' $ALL || true)"
USER_FILES="$(printf '%s\n' $ALL | grep -vxF -f <(printf '%s\n' $SCRATCH) || true)"

chunks() { # group files... -> prints "group<TAB>index<TAB>file file ..."
    local group="$1"; shift
    printf '%s\n' "$@" | grep -v '^$' | xargs -n "$CHUNK" | awk -v g="$group" '{print g "\t" NR "\t" $0}'
}

PLAN="$(
    chunks IVG236 $SCRATCH
    chunks IVGFHIR $FHIR
    chunks USER $USER_FILES
)"

if [ "${1:-}" = "--list" ]; then
    printf '%s\n' "$PLAN" | awk -F'\t' '{n=split($3, f, " "); print $1, "chunk", $2, "(" n " files)"}'
    exit 0
fi

OUT="${1:-$(mktemp -d "${TMPDIR:-/tmp}/ivg-linecov.XXXXXX")}"
mkdir -p "$OUT"
ROUTINES="$(python3 -c 'import sys; sys.path.insert(0, "."); from scripts.line_coverage import linecov; print(",".join(linecov.routine_names()))')"

wait_healthy() {
    for _ in $(seq 1 60); do
        docker ps --format '{{.Names}} {{.Status}}' | grep -q "^$CONTAINER .*healthy" && return 0
        sleep 5
    done
    echo "container $CONTAINER did not become healthy" >&2; return 1
}

session() { docker exec -i "$CONTAINER" iris session IRIS -U "$1"; }

docker cp scripts/line_coverage/Cov.cls "$CONTAINER:/tmp/Cov.cls"
printf '%s\n' "$PLAN" | while IFS=$'\t' read -r group index files; do
    echo "=== $group chunk $index: $(wc -w <<<"$files") files"
    docker restart -t 90 "$CONTAINER" >/dev/null </dev/null; wait_healthy
    printf 'write $system.OBJ.Load("/tmp/Cov.cls","ck"),!\nhalt\n' | session "$group" >/dev/null
    dump="/tmp/linecov_${group}_${index}.out"
    docker exec "$CONTAINER" rm -f /tmp/cov.stop "$dump"
    (printf 'do ##class(IVG236Tmp.Cov).Run("%s","/tmp/cov.stop","%s")\nhalt\n' "$ROUTINES" "$dump" | session "$group" >/dev/null 2>&1 &)
    sleep 10
    scripts/enterprise-container.sh tcp-load-arno >/dev/null 2>&1 </dev/null || true
    for f in $files; do
        .venv/bin/python -m pytest "$f" -q -p no:cacheprovider </dev/null 2>&1 | tail -1 | sed "s#^#$f :: #"
    done
    docker exec "$CONTAINER" touch /tmp/cov.stop
    for _ in $(seq 1 100); do
        docker exec "$CONTAINER" tail -1 "$dump" 2>/dev/null | grep -q DONE && break
        sleep 3
    done
    docker cp "$CONTAINER:$dump" "$OUT/${group}_${index}.out"
done

echo "=== gate"
.venv/bin/python scripts/line_coverage/check_gate.py "$OUT"/*.out
