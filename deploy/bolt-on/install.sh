#!/usr/bin/env bash
set -euo pipefail

HOST="${1:-localhost}"
PORT="${2:-1972}"
NAMESPACE="${3:-IVG}"
ADMIN_USER="${4:-_SYSTEM}"

echo "IVG Bolt-On Installer"
echo "====================="
echo "Target: ${HOST}:${PORT}"
echo "Namespace: ${NAMESPACE}"
echo ""

read -rsp "Admin password for ${ADMIN_USER}@${HOST}: " ADMIN_PASS
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IVG_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "1. Generating service user password..."
IVG_SERVICE_PASS="$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")"

echo "2. Generating API key..."
IVG_API_KEY="$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")"

echo "3. Connecting to IRIS..."
python3 - << PYEOF
import warnings; warnings.filterwarnings('ignore')
import iris

conn = iris.connect(hostname='${HOST}', port=${PORT}, namespace='%SYS',
                    username='${ADMIN_USER}', password='${ADMIN_PASS}')
cursor = conn.cursor()

try:
    cursor.execute("SELECT COUNT(*) FROM %SYS.Namespace WHERE Name = '${NAMESPACE}'")
    exists = cursor.fetchone()[0]
    if not exists:
        print(f"   Creating namespace ${NAMESPACE}...")
        import iris as _i
        iris_obj = _i.createIRIS(conn)
        iris_obj.classMethodValue('%SYS.Namespace', 'Create', '${NAMESPACE}', '${NAMESPACE}')
        print(f"   Namespace ${NAMESPACE} created.")
    else:
        print(f"   Namespace ${NAMESPACE} already exists.")
except Exception as e:
    print(f"   WARNING: Could not verify/create namespace: {e}")
    print(f"   You may need to create namespace ${NAMESPACE} manually in Management Portal.")

conn.close()
PYEOF

echo "4. Installing IVG Python package..."
pip install "iris-vector-graph[full]" -q

echo "5. Initializing IVG schema..."
python3 - << PYEOF
import warnings; warnings.filterwarnings('ignore')
import iris
from iris_vector_graph.engine import IRISGraphEngine

conn = iris.connect(hostname='${HOST}', port=${PORT}, namespace='${NAMESPACE}',
                    username='${ADMIN_USER}', password='${ADMIN_PASS}')
eng = IRISGraphEngine(conn, embedding_dimension=768)
result = eng.initialize_schema(auto_deploy_objectscript=True)
print(f"   Schema: {result}")
conn.close()
PYEOF

echo "6. Writing config..."
cat > "${IVG_ROOT}/ivg-config.yml" << YAML
iris:
  host: ${HOST}
  port: ${PORT}
  namespace: ${NAMESPACE}
  username: ivg_service
  password: ${IVG_SERVICE_PASS}

server:
  host: 0.0.0.0
  port: 8200

auth:
  mode: api_key
  api_key: ${IVG_API_KEY}

schema:
  auto_init: true
  embedding_dimension: 768
YAML

echo ""
echo "Done!"
echo ""
echo "Start the IVG server with:"
echo "  IVG_API_KEY='${IVG_API_KEY}' ivg server start --iris-host ${HOST} --iris-port ${PORT} --iris-namespace ${NAMESPACE} --iris-password '${IVG_SERVICE_PASS}'"
echo ""
echo "Or:"
echo "  ivg server start  # (uses ivg-config.yml)"
echo ""
echo "API key (save this): ${IVG_API_KEY}"
