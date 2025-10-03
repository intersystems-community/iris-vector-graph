#!/bin/bash
# Fraud Scoring Server Startup Script (Embedded Python)
#
# This script runs in IRIS container and starts the fraud scoring API
# via irispython (embedded Python with iris module).

set -e

echo "[Fraud Server] Starting IRIS..."

# Start IRIS in background with CPF merge
/iris-main --check-caps false -a "iris merge IRIS /home/irisowner/app/merge.cpf" &
IRIS_PID=$!

echo "[Fraud Server] IRIS PID: $IRIS_PID"

# Wait for IRIS to be fully started
echo "[Fraud Server] Waiting for IRIS to be ready..."
max_wait=120
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

# Additional wait for stability and CPF merge to complete
sleep 10

# Load fraud schema via irispython
echo "[Fraud Server] Loading fraud schema..."
echo "[Fraud Server] Python version: $(/usr/irissys/bin/irispython --version 2>&1)"
echo "[Fraud Server] Testing iris module..."
/usr/irissys/bin/irispython -c "import iris; print('iris module loaded successfully')" 2>&1 || echo "Failed to load iris module"
/usr/irissys/bin/irispython /home/irisowner/app/scripts/fraud/load_fraud_schema_embedded.py 2>&1 | tee /tmp/schema-load.log

if [ $? -ne 0 ]; then
    echo "[Fraud Server] WARNING: Schema loading failed, continuing anyway..."
    cat /tmp/schema-load.log
fi

# Load sample data (if requested)
if [ "$LOAD_SAMPLE_DATA" = "true" ]; then
    echo "[Fraud Server] Loading sample fraud data..."
    /usr/irissys/bin/irispython /home/irisowner/app/scripts/fraud/load_sample_events_embedded.py 2>&1 | tee /tmp/sample-load.log

    if [ $? -ne 0 ]; then
        echo "[Fraud Server] WARNING: Sample data loading failed, continuing anyway..."
        cat /tmp/sample-load.log
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
