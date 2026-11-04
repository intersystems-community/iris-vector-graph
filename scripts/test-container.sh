#!/usr/bin/env bash
# Persistent test container management for iris-vector-graph (Spec 162 + all future specs).
#
# This is THE entry point for IRIS test container ops. Never use raw docker
# commands or IRISContainer.start() directly — those create ephemeral containers
# that vanish when the parent Python process exits.
#
# Usage:
#   scripts/test-container.sh up        # Start ivg-iris (persistent, idempotent)
#   scripts/test-container.sh down      # Stop and remove ivg-iris
#   scripts/test-container.sh status    # Check health
#   scripts/test-container.sh deploy    # Copy iris_src/src/ to /tmp/src (/tmp/src_<NS> for IVG_NAMESPACE)
#   scripts/test-container.sh compile <ClassName>   # Load and compile one class into USER (or IVG_NAMESPACE)
#   scripts/test-container.sh compile-all          # Load and compile the whole of it into USER (or IVG_NAMESPACE)
#
# Constitution Principle IV grounding: container name is `ivg-iris` (replaces
# legacy `gqs-ivg-test` 2026-05-28 — gqs predates the iris-vector-graph rename).
# Verified against tests/conftest.py:_GQS_CONTAINER and the lab_manager registry.
# Never change without updating all three in lockstep.

set -euo pipefail

CONTAINER="${IVG_TEST_CONTAINER:-ivg-iris}"
EDITION="community"
# Port 21972: avoids conflict with localhost:1972 SSH tunnel to los-iris (productivity-framework).
# Registry: ~/ws/productivity-framework/tools/lab_manager/config/iris-container-registry.yaml
# ivg-iris host_port: 21972, web_port: 32779 (or auto-assigned)
CONTAINER_PORT="${IVG_CONTAINER_PORT:-21972}"

cmd="${1:-status}"

# The namespace deploy, compile and compile-all stage for and load into (IVG_NAMESPACE, USER by default), and its
# staging directory, tests/conftest.py's: /tmp/src for USER, /tmp/src_<NS> for a scratch one (as R185 made
# scripts/enterprise-container.sh do).
NS="${IVG_NAMESPACE:-USER}"
if [ "$NS" = "USER" ]; then STAGE="/tmp/src"; else STAGE="/tmp/src_${NS}"; fi

# 4.6.0 R197: compile and compile-all load iris_src/src as written, and it names Graph_KG. Before loading, switch
# to NS and exit 3 there if NS records another IVG Schema: scripts/enterprise-container.sh's check (R185).
REFUSE_OTHER_SCHEMA_PY="
import iris, os, sys
ns = os.environ['IVG_NS']
iris.system.Process.SetNamespace(ns)
recorded = iris.gref('^IVG.Deploy').get(['schema'], '')
if recorded not in (None, '') and str(recorded).upper() != 'GRAPH_KG':
    print(f'{ns} records {recorded} as its IVG Schema, and these sources name Graph_KG. Nothing was loaded. '
          f'Deploy with IRISGraphEngine(conn, schema_prefix={str(recorded)!r}).initialize_schema().', file=sys.stderr)
    sys.exit(3)
"

# Run the check in NS; on a refusal say so and stop, having loaded nothing.
refuse_other_schema() {
  _rc=0
  _out=$(docker exec -e IVG_NS="$NS" "$CONTAINER" /usr/irissys/bin/irispython -c "$REFUSE_OTHER_SCHEMA_PY" 2>&1) || _rc=$?
  if [ "$_rc" -ne 0 ]; then
    echo "$_out"
    echo "✗ $1: nothing loaded into $NS (the IVG Schema check exited $_rc)"
    exit 1
  fi
}

case "$cmd" in
  up)
    if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
      echo "✓ $CONTAINER already running"
      exit 0
    fi
    echo "Starting $CONTAINER (edition=$EDITION) via iris-devtester..."
    idt container up --name "$CONTAINER" --edition "$EDITION" --port "$CONTAINER_PORT"
    echo "Waiting for IRIS to be ready..."
    sleep 15
    IVG_NAMESPACE=USER "$0" deploy
    echo "Initializing schema (required before compile-all — &sql macros reference Graph_KG tables)..."
    python3 -c "
from iris_devtester import IRISContainer
c = IRISContainer.attach('$CONTAINER')
conn = c.get_connection()
from iris_vector_graph import IRISGraphEngine
# 768 matches schema.py's own default (get_base_schema_sql) and what most of
# the e2e/integration suite assumes for kg_NodeEmbeddings.
IRISGraphEngine(conn, embedding_dimension=768).initialize_schema()
print('✓ schema initialized')
" 2>&1 | grep -E 'schema initialized|ERROR|CRITICAL' | grep -v 'Embedding dimension'
    echo "Recompiling Graph.KG.* after container start..."
    IVG_NAMESPACE=USER "$0" compile-all 2>&1 | grep -v '^$' | grep -iE 'ERROR|Finish' | grep -v '%AI\|Graph.KG.Meta\|User.PageRankEmbed\|TestEdge' | head -5 || true
    # Recompile split-class facades AFTER their sibling impl classes (dependency order).
    # compile-all uses alphabetical order: Traversal.cls compiles before TraversalBuild.cls,
    # leaving the facade's .1 routine with unresolved references. Fix by recompiling in order.
    for _cls in Graph.KG.TraversalBuild Graph.KG.TraversalBFS Graph.KG.TraversalPaths Graph.KG.TraversalKHop Graph.KG.Traversal Graph.KG.NKGAccelLoader Graph.KG.NKGAccelAdjacency Graph.KG.NKGAccelTraversal Graph.KG.NKGAccelCentrality Graph.KG.NKGAccel; do
      IVG_NAMESPACE=USER "$0" compile "$_cls" > /dev/null 2>&1 || true
    done
    "$(dirname "$0")/install-embedded-deps.sh" "$CONTAINER" || true
    echo "✓ $CONTAINER ready"
    ;;

  down)
    if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
      # Bug T defense: graceful IRIS shutdown flushes the write image journal
      # before SIGKILL, preventing silent row loss on next start. Required even
      # for `rm -f` because forced removal still SIGKILLs without a flush.
      if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
        docker exec "$CONTAINER" iris stop IRIS quietly 2>/dev/null || true
      fi
      docker rm -f "$CONTAINER"
      echo "✓ $CONTAINER removed (clean IRIS shutdown)"
    else
      echo "(no $CONTAINER container present)"
    fi
    ;;

  status)
    if docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}' | grep "^$CONTAINER\b"; then
      exit 0
    else
      echo "✗ $CONTAINER NOT RUNNING — run 'scripts/test-container.sh up'"
      exit 1
    fi
    ;;

  deploy)
    if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
      echo "✗ $CONTAINER not running. Run 'scripts/test-container.sh up' first."
      exit 1
    fi
    docker exec "$CONTAINER" mkdir -p "$STAGE"
    docker cp iris_src/src/. "$CONTAINER":"$STAGE"/
    echo "✓ deployed iris_src/src/ → $CONTAINER:$STAGE/ (loads nothing: compile-all loads it into $NS)"
    ;;

  compile)
    cls="${2:-}"
    if [ -z "$cls" ]; then
      echo "Usage: $0 compile <ClassName>   (e.g. Graph.KG.Centrality)"
      exit 1
    fi
    refuse_other_schema compile
    docker exec "$CONTAINER" bash -c "echo 'Do \$system.OBJ.Load(\"$STAGE/${cls//.//}.cls\",\"ck\")
H' | iris session iris -U \"$NS\""
    ;;

  compile-all)
    refuse_other_schema compile-all
    docker exec "$CONTAINER" bash -c "echo 'Do \$system.OBJ.LoadDir(\"$STAGE\",\"ck\",.err,1)
H' | iris session iris -U \"$NS\""
    ;;

  *)
    echo "Usage: $0 {up|down|status|deploy|compile <Class>|compile-all}"
    echo "  IVG_NAMESPACE names the namespace deploy, compile and compile-all work in (USER by default);"
    echo "  compile and compile-all refuse one recorded under another IVG Schema"
    exit 1
    ;;
esac
