#!/usr/bin/env bash
# Start, stop and inspect the comparison databases. Never touches an IRIS container.
#
#   competitors.sh start  <neo4j|memgraph|both> [DATASET]   default dataset S
#   competitors.sh stop   <neo4j|memgraph|both>             graceful; volumes are kept
#   competitors.sh status
#   competitors.sh gds                                      fetch the GDS jar into the cache (once)
#   competitors.sh reset  <neo4j|memgraph> <DATASET> --yes  delete that dataset's volume
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE=(docker compose -f "$HERE/docker-compose.yml")
export IVG_BENCH_HOME="${IVG_BENCH_HOME:-$HOME/.cache/ivg-bench}"
GDS_VERSION="${GDS_VERSION:-2.12.0}" # the GDS release for Neo4j 5.24 (graphdatascience.ninja/versions.json)
GDS_JAR="neo4j-graph-data-science-${GDS_VERSION}.jar"

usage() { sed -n '2,9p' "${BASH_SOURCE[0]}"; exit 2; }

services_for() {
  case "$1" in
    neo4j) echo "neo4j" ;;
    memgraph) echo "memgraph" ;;
    both) echo "neo4j memgraph" ;;
    *) usage ;;
  esac
}

container_for() { case "$1" in neo4j) echo ivg-neo4j ;; memgraph) echo ivg-memgraph ;; esac; }

running_dataset() { # prints the dataset of the volume mounted in a running container, or nothing
  local c="$1"
  docker inspect -f '{{range .Mounts}}{{.Name}} {{end}}' "$c" 2>/dev/null | tr ' ' '\n' | sed -En 's/^ivg-(neo4j|memgraph)-data-//p' | head -1 || true
}

case "${1:-}" in
  start)
    engine="${2:-}"; ds="${3:-S}"
    [[ -n "$engine" ]] || usage
    [[ "$ds" =~ ^[A-Za-z0-9_.-]+$ ]] || { echo "bad dataset name: $ds" >&2; exit 2; }
    export IVG_DS="$ds"
    for svc in $(services_for "$engine"); do
      c="$(container_for "$svc")"
      cur="$(running_dataset "$c")"
      if [[ -n "$cur" && "$cur" != "$ds" ]]; then
        echo "$c is running dataset $cur; stopping it first"
        "${COMPOSE[@]}" stop -t 180 "$svc"
      fi
    done
    if [[ "$engine" != memgraph && ! -f "$IVG_BENCH_HOME/neo4j-plugins/$GDS_JAR" ]]; then
      echo "note: no GDS jar in $IVG_BENCH_HOME/neo4j-plugins; run '$0 gds' first for the GDS legs" >&2
      mkdir -p "$IVG_BENCH_HOME/neo4j-plugins"
    fi
    # shellcheck disable=SC2046
    "${COMPOSE[@]}" up -d --wait $(services_for "$engine")
    echo "started $engine on dataset $ds (volume ivg-<engine>-data-$ds)"
    ;;
  stop)
    engine="${2:-}"
    [[ -n "$engine" ]] || usage
    # shellcheck disable=SC2046
    "${COMPOSE[@]}" stop -t 180 $(services_for "$engine")
    echo "stopped $engine; volumes kept"
    ;;
  status)
    for c in ivg-neo4j ivg-memgraph; do
      if docker inspect "$c" >/dev/null 2>&1; then
        st="$(docker inspect -f '{{.State.Status}} (health: {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}})' "$c")"
        echo "$c: $st  dataset=$(running_dataset "$c")  image=$(docker inspect -f '{{.Config.Image}}' "$c")"
      else
        echo "$c: not created"
      fi
    done
    echo "ports: neo4j bolt 7688 (http 7475), memgraph bolt 7689"
    echo "volumes:"
    docker volume ls --format '{{.Name}}' | grep -E '^ivg-(neo4j|memgraph)-data-' | sed 's/^/  /' || echo "  (none)"
    if [[ -f "$IVG_BENCH_HOME/neo4j-plugins/$GDS_JAR" ]]; then
      echo "GDS jar: $IVG_BENCH_HOME/neo4j-plugins/$GDS_JAR"
    else
      echo "GDS jar: absent (run '$0 gds')"
    fi
    ;;
  gds)
    mkdir -p "$IVG_BENCH_HOME/neo4j-plugins"
    if [[ -f "$IVG_BENCH_HOME/neo4j-plugins/$GDS_JAR" ]]; then
      echo "already present: $IVG_BENCH_HOME/neo4j-plugins/$GDS_JAR"
    else
      curl -fsSL -o "$IVG_BENCH_HOME/neo4j-plugins/$GDS_JAR" "https://graphdatascience.ninja/$GDS_JAR"
    fi
    shasum -a 256 "$IVG_BENCH_HOME/neo4j-plugins/$GDS_JAR"
    ;;
  reset)
    engine="${2:-}"; ds="${3:-}"; flag="${4:-}"
    [[ -n "$engine" && -n "$ds" && "$flag" == "--yes" ]] || usage
    c="$(container_for "$engine")"
    [[ -z "$(docker ps -q -f "name=^${c}$")" ]] || { echo "stop $c first" >&2; exit 1; }
    docker rm "$c" >/dev/null 2>&1 || true
    docker volume rm "ivg-${engine}-data-${ds}"
    ;;
  *) usage ;;
esac
