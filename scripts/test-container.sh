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
#   scripts/test-container.sh deploy    # Deploy iris_src/src/ to /tmp/src in container
#   scripts/test-container.sh compile <ClassName>   # Compile a class in container
#   scripts/test-container.sh compile-all          # Compile entire Graph.KG.* package
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
    "$0" deploy
    echo "Initializing schema (required before compile-all — &sql macros reference Graph_KG tables)..."
    python3 -c "
from iris_devtester import IRISContainer
c = IRISContainer.attach('$CONTAINER')
conn = c.get_connection()
from iris_vector_graph import IRISGraphEngine
IRISGraphEngine(conn, embedding_dimension=128).initialize_schema()
print('✓ schema initialized')
" 2>&1 | grep -E 'schema initialized|ERROR|CRITICAL' | grep -v 'Embedding dimension'
    echo "Recompiling Graph.KG.* after container start..."
    "$0" compile-all 2>&1 | grep -v '^$' | grep -iE 'ERROR|Finish' | grep -v '%AI\|Graph.KG.Meta\|User.PageRankEmbed\|TestEdge' | head -5 || true
    # Recompile split-class facades AFTER their sibling impl classes (dependency order).
    # compile-all uses alphabetical order: Traversal.cls compiles before TraversalBuild.cls,
    # leaving the facade's .1 routine with unresolved references. Fix by recompiling in order.
    for _cls in Graph.KG.TraversalBuild Graph.KG.TraversalBFS Graph.KG.TraversalPaths Graph.KG.TraversalKHop Graph.KG.Traversal Graph.KG.NKGAccelLoader Graph.KG.NKGAccelAdjacency Graph.KG.NKGAccelTraversal Graph.KG.NKGAccelCentrality Graph.KG.NKGAccel; do
      "$0" compile "$_cls" > /dev/null 2>&1 || true
    done
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
    docker exec "$CONTAINER" mkdir -p /tmp/src
    docker cp iris_src/src/. "$CONTAINER":/tmp/src/
    echo "✓ deployed iris_src/src/ → $CONTAINER:/tmp/src/"
    ;;

  compile)
    cls="${2:-}"
    if [ -z "$cls" ]; then
      echo "Usage: $0 compile <ClassName>   (e.g. Graph.KG.Centrality)"
      exit 1
    fi
    docker exec "$CONTAINER" bash -c "echo 'Do \$system.OBJ.Load(\"/tmp/src/${cls//.//}.cls\",\"ck\")
H' | iris session iris -U USER"
    ;;

  compile-all)
    docker exec "$CONTAINER" bash -c 'echo "Do \$system.OBJ.LoadDir(\"/tmp/src\",\"ck\",.err,1)
H" | iris session iris -U USER'
    ;;

  *)
    echo "Usage: $0 {up|down|status|deploy|compile <Class>|compile-all}"
    exit 1
    ;;
esac
