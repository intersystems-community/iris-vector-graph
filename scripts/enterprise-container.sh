#!/usr/bin/env bash
# Enterprise test container management for iris-vector-graph (Spec 190).
# Manages ivg-iris-enterprise (Enterprise IRIS + libarno_callout.so).
# Paired with scripts/test-container.sh (Community ivg-iris).
#
# Usage:
#   scripts/enterprise-container.sh up        # Start ivg-iris-enterprise, deploy, init, load arno
#   scripts/enterprise-container.sh down      # Stop and remove ivg-iris-enterprise
#   scripts/enterprise-container.sh status    # Check health + arno loaded
#   scripts/enterprise-container.sh deploy    # Deploy iris_src/src/ to /tmp/src in container
#
# Constitution Principle IV grounding:
#   Container: ivg-iris-enterprise  (Registry: iris-vector-graph-enterprise entry)
#   Port:      31972 host → 1972 container
#   .so:       docker/enterprise/libarno_callout.so → /tmp/libarno_callout.so inside container
#   Verified against docker/enterprise/docker-compose.yml and lab_manager registry.

set -euo pipefail

CONTAINER="${IVG_ARNO_CONTAINER:-ivg-iris-enterprise}"
COMPOSE_FILE="docker/enterprise/docker-compose.yml"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

cmd="${1:-status}"

case "$cmd" in
  up)
    if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
      echo "✓ $CONTAINER already running"
      exit 0
    fi
    echo "Starting $CONTAINER via docker compose..."
    docker compose -f "$REPO_ROOT/$COMPOSE_FILE" up -d
    echo "Waiting for IRIS to be ready (up to 3 min)..."
    for i in $(seq 1 36); do
      if docker ps --filter "name=$CONTAINER" --filter "health=healthy" --format '{{.Names}}' | grep -qx "$CONTAINER" 2>/dev/null; then
        echo "  container healthy after ${i}×5s"
        break
      fi
      sleep 5
    done
    "$0" deploy
    echo "Initializing schema..."
    python3 -c "
import subprocess, iris
ip = subprocess.run(['docker','inspect','$CONTAINER','--format',
    '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'],
    capture_output=True, text=True).stdout.strip()
if ip:
    conn = iris.connect(hostname=ip, port=1972, namespace='USER', username='_SYSTEM', password='SYS')
else:
    from iris_devtester import IRISContainer as C
    c = C.attach('$CONTAINER'); c._connection = None; conn = c.get_connection()
from iris_vector_graph import IRISGraphEngine
IRISGraphEngine(conn, embedding_dimension=128).initialize_schema()
print('✓ schema initialized')
" 2>&1 | grep -E 'schema initialized|ERROR|CRITICAL' | grep -v 'Embedding dimension'
    echo "Deploying and compiling ObjectScript..."
    "$0" compile-all 2>&1 | grep -v '^$' | grep -iE 'ERROR|Finish' | grep -v '%AI\|Graph.KG.Meta\|User.PageRankEmbed\|TestEdge' | head -5 || true
    docker exec -i "$CONTAINER" iris session IRIS -U USER <<'OSEOF' > /dev/null 2>&1 || true
Do $system.OBJ.Delete("Graph.KG.funckgDegreeCentrality","-d")
Do $system.OBJ.Load("/tmp/src/Graph/KG/Centrality.cls","ck")
H
OSEOF
    for _cls in Graph.KG.ArnoAccel Graph.KG.TraversalBuild Graph.KG.TraversalBFS Graph.KG.TraversalPaths Graph.KG.TraversalKHop Graph.KG.Traversal Graph.KG.NKGAccelLoader Graph.KG.NKGAccelAdjacency Graph.KG.NKGAccelTraversal Graph.KG.NKGAccelCentrality Graph.KG.NKGAccel; do
      "$0" compile "$_cls" > /dev/null 2>&1 || true
    done
    echo "Loading libarno_callout.so..."
    python3 -c "
import subprocess, iris, json
ip = subprocess.run(['docker','inspect','$CONTAINER','--format',
    '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'],
    capture_output=True, text=True).stdout.strip()
if ip:
    conn = iris.connect(hostname=ip, port=1972, namespace='USER', username='_SYSTEM', password='SYS')
else:
    from iris_devtester import IRISContainer as C
    c = C.attach('$CONTAINER'); c._connection = None; conn = c.get_connection()
irisobj = iris.createIRIS(conn)
# Load via ArnoAccel (rzf-style, sets rust_callout capability)
r1 = irisobj.classMethodValue('Graph.KG.ArnoAccel', 'Load', '/tmp/libarno_callout.so')
# Also load via NKGAccelLoader for legacy compat
irisobj.classMethodValue('Graph.KG.NKGAccelLoader', 'Load', '/tmp/libarno_callout.so')
# Verify rust_callout is now True
caps = json.loads(str(irisobj.classMethodValue('Graph.KG.NKGAccel', 'Capabilities')))
if caps.get('rust_callout'):
    print('✓ libarno_callout.so loaded (rust_callout=True, bfs=True)')
elif r1:
    print('⚠ libarno_callout.so loaded but rust_callout=False (capabilities:', caps.get('rust_callout'), ')')
else:
    print('✗ libarno_callout.so load FAILED — check /tmp/libarno_callout.so exists in container')
    exit(1)
" 2>&1 | grep -vE 'swigvarlink|IVG setup|Deprecat'
    "$(dirname "$0")/install-embedded-deps.sh" "$CONTAINER" || true
    echo "✓ $CONTAINER ready (Enterprise + Arno)"
    ;;

  down)
    echo "Stopping $CONTAINER..."
    docker compose -f "$REPO_ROOT/$COMPOSE_FILE" down
    echo "✓ $CONTAINER stopped"
    ;;

  status)
    if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
      echo "$CONTAINER	$(docker ps --filter "name=$CONTAINER" --format '{{.Status}}')"
      python3 -c "
import subprocess, iris, json
ip = subprocess.run(['docker','inspect','$CONTAINER','--format',
    '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'],
    capture_output=True, text=True).stdout.strip()
try:
    if ip:
        conn = iris.connect(hostname=ip, port=1972, namespace='USER', username='_SYSTEM', password='SYS')
    else:
        from iris_devtester import IRISContainer as C
        c = C.attach('$CONTAINER'); c._connection = None; conn = c.get_connection()
    irisobj = iris.createIRIS(conn)
    # Load arno to get accurate rust_callout status
    try: irisobj.classMethodValue('Graph.KG.ArnoAccel', 'Load', '/tmp/libarno_callout.so')
    except: pass
    caps = irisobj.classMethodValue('Graph.KG.NKGAccel','Capabilities')
    d = json.loads(str(caps))
    print('  arno rust_callout:', d.get('rust_callout', False))
    print('  arno bfs:', d.get('bfs', False))
except Exception as e:
    print('  arno status: unavailable -', str(e)[:60])
" 2>&1 | grep -vE 'swigvarlink|IVG setup|Deprecat'
    else
      echo "$CONTAINER not running (start with: scripts/enterprise-container.sh up)"
      exit 1
    fi
    ;;

  deploy)
    echo "Deploying iris_src/src/ to $CONTAINER..."
    docker exec "$CONTAINER" mkdir -p /tmp/src 2>/dev/null || true
    docker cp "$REPO_ROOT/iris_src/src/." "$CONTAINER:/tmp/src/"
    echo "✓ deployed iris_src/src/ → $CONTAINER:/tmp/src/"
    ;;

  compile)
    cls="${2:-}"
    if [ -z "$cls" ]; then echo "Usage: $0 compile <ClassName>"; exit 1; fi
    echo -n "Compiling $cls... "
    docker exec -i "$CONTAINER" iris session IRIS -U USER \
      "##class(%SYSTEM.OBJ).Load(\"/tmp/src/$(echo "$cls" | tr '.' '/').cls\",\"ck\")" \
      2>/dev/null | grep -iE 'ERROR|Load finished' | head -1
    ;;

  compile-all)
    echo "Compiling all Graph.KG.* classes..."
    docker exec -i "$CONTAINER" iris session IRIS -U USER \
      'Do $system.OBJ.LoadDir("/tmp/src","ck",.err,1)' \
      2>/dev/null | grep -iE 'ERROR|Load finished|Detected' | grep -v 'Warning'
    ;;

  *)
    echo "Usage: $0 {up|down|status|deploy|compile <cls>|compile-all}"
    exit 1
    ;;
esac
