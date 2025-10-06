#!/bin/bash
# Fraud API Server Startup (IRIS already running)
set +e

echo "[Fraud API] IRIS should already be running"
echo "[Fraud API] Starting fraud scoring API..."

# Export IRIS credentials for embedded Python
export IRISUSERNAME=${IRIS_USERNAME:-_SYSTEM}
export IRISPASSWORD=${IRIS_PASSWORD:-SYS}
export IRISNAMESPACE=${IRIS_NAMESPACE:-USER}

cd /home/irisowner/app/src

# Run fraud API server via irispython
export PYTHONDONTWRITEBYTECODE=1
exec /usr/irissys/bin/irispython -m iris_fraud_server 2>&1 | tee /tmp/fraud-server.log
