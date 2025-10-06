#!/bin/bash
# Fraud Scoring Server Startup Script (Embedded Python)
#
# This script runs in IRIS container and starts the fraud scoring API
# via irispython (embedded Python with iris module).

# Removed 'set -e' to allow script to continue even if some commands fail
# This is necessary because iris module import may fail initially before CallIn is enabled
set +e

echo "[Fraud Server] Starting IRIS..."

# Start IRIS in background
/iris-main --check-caps false &
IRIS_PID=$!

echo "[Fraud Server] IRIS PID: $IRIS_PID"

# Wait for IRIS to be fully started
echo "[Fraud Server] Waiting for IRIS to be ready..."
max_wait=1200  # 20 minutes for large database recovery (29GB+)
count=0
while [ $count -lt $max_wait ]; do
    # Check if IRIS is ready by looking for the "started InterSystems IRIS" message in logs
    # or check if port 1972 is listening using /proc
    if [ -e /proc/net/tcp ] && grep -q ":07B4 " /proc/net/tcp 2>/dev/null; then
        echo "[Fraud Server] IRIS is ready (port 1972 listening)!"
        break
    fi
    count=$((count + 1))
    sleep 1
done

if [ $count -eq $max_wait ]; then
    echo "[Fraud Server] ERROR: IRIS failed to start within ${max_wait}s"
    kill $IRIS_PID 2>/dev/null || true
    exit 1
fi

# Additional wait for stability
sleep 10

# Enable CallIn service via ObjectScript (required for embedded Python)
echo "[Fraud Server] Enabling CallIn service with all authentication methods..."
echo -e "_SYSTEM\nSYS\nset sc = ##class(Security.Services).Get(\"%Service_CallIn\", .Properties)\nset Properties(\"Enabled\") = 1\nset Properties(\"AutheEnabled\") = 64\nset sc = ##class(Security.Services).Modify(\"%Service_CallIn\", .Properties)\nwrite \"CallIn service enabled: \", sc,!\nhalt" | /usr/irissys/bin/irissession IRIS -U%SYS

# Wait for service to activate
echo "[Fraud Server] Waiting for CallIn service to activate..."
sleep 5

# Export IRIS credentials for embedded Python
export IRISUSERNAME=${IRIS_USERNAME:-_SYSTEM}
export IRISPASSWORD=${IRIS_PASSWORD:-SYS}
export IRISNAMESPACE=${IRIS_NAMESPACE:-USER}

# Verify database is accessible
echo "[Fraud Server] Verifying database access..."
/usr/irissys/bin/irispython -c "
import iris
result = iris.sql.exec('SELECT COUNT(*) FROM gs_events')
count = list(result)[0][0]
print(f'[Fraud Server] ✅ Database verified: {count:,} transactions')
" 2>&1 || echo "[Fraud Server] ⚠️  Database verification failed"

# Load fraud schema via irispython (skip if database already configured)
echo "[Fraud Server] Checking if schema needs to be loaded..."
echo "[Fraud Server] Python version: $(/usr/irissys/bin/irispython --version 2>&1)"
echo "[Fraud Server] Testing iris module..."
/usr/irissys/bin/irispython -c "import iris; print('iris module loaded successfully')" 2>&1 || echo "Failed to load iris module"
/usr/irissys/bin/irispython /home/irisowner/app/scripts/fraud/load_fraud_schema_embedded.py 2>&1 | tee /tmp/schema-load.log || true

if [ $? -ne 0 ]; then
    echo "[Fraud Server] Schema loading skipped (database may already be configured)"
fi

# Load sample data (if requested and database is empty)
if [ "$LOAD_SAMPLE_DATA" = "true" ]; then
    echo "[Fraud Server] Checking if sample data needs to be loaded..."
    /usr/irissys/bin/irispython /home/irisowner/app/scripts/fraud/load_sample_events_embedded.py 2>&1 | tee /tmp/sample-load.log || true

    if [ $? -ne 0 ]; then
        echo "[Fraud Server] Sample data loading skipped (database may already have data)"
    fi
fi

# Clear Python cache BEFORE starting server (ensure code is fresh)
# CRITICAL: Must clear cache at package level to avoid stale module imports
echo "[Fraud Server] Clearing Python cache..."
find /home/irisowner/app/src -name '*.pyc' -delete 2>/dev/null || true
find /home/irisowner/app/src -type d -name '__pycache__' -print0 2>/dev/null | xargs -0 rm -rf || true

echo "[Fraud Server] Verifying cache cleared..."
pyc_count=$(find /home/irisowner/app/src -name '*.pyc' 2>/dev/null | wc -l)
pycache_count=$(find /home/irisowner/app/src -type d -name '__pycache__' 2>/dev/null | wc -l)
echo "[Fraud Server] Remaining .pyc files: $pyc_count"
echo "[Fraud Server] Remaining __pycache__ dirs: $pycache_count"

# Install dependencies for irispython (iris-pgwire pattern)
echo "[Fraud Server] Installing Python dependencies via irispython..."
/usr/irissys/bin/irispython -m pip install --quiet --break-system-packages --user \
    fastapi uvicorn structlog torch numpy pydantic 2>&1 | grep -v "WARNING:" || true

# Start fraud API server via irispython
echo "[Fraud Server] Starting FastAPI server via irispython..."
echo "[Fraud Server] Port: ${FRAUD_API_PORT:-8000}"
echo "[Fraud Server] Model: ${FRAUD_MODEL_PATH}"
echo "[Fraud Server] Logs: /tmp/fraud-server.log"
echo "[Fraud Server] PYTHONDONTWRITEBYTECODE=1 (prevent .pyc creation)"

cd /home/irisowner/app/src

# Run server via irispython (NOT system python!)
# CRITICAL: Must use /usr/irissys/bin/irispython to have iris module
# Keep IRIS running by running server in foreground
# Set PYTHONDONTWRITEBYTECODE to prevent .pyc creation
export PYTHONDONTWRITEBYTECODE=1
exec /usr/irissys/bin/irispython -m iris_fraud_server 2>&1 | tee /tmp/fraud-server.log
