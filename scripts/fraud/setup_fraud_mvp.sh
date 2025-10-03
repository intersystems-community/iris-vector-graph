#!/bin/bash
# Fraud Scoring MVP - Complete Setup Script
#
# This script sets up the fraud scoring MVP from scratch:
# 1. Starts dedicated IRIS instance (port 41972)
# 2. Loads fraud schema (tables + procedures)
# 3. Loads sample data (100 entities, 1000 events)
# 4. Starts FastAPI server
# 5. Validates end-to-end functionality
#
# Usage: ./scripts/fraud/setup_fraud_mvp.sh

set -e  # Exit on any error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"

echo "=========================================="
echo " Fraud Scoring MVP - Complete Setup"
echo "=========================================="
echo ""

# Step 1: Start IRIS instance
echo "Step 1: Starting dedicated IRIS instance for fraud MVP..."
echo "Port mapping: 41972 (SuperServer), 452773 (Management Portal)"

if docker ps | grep -q iris-fraud-mvp; then
    echo "✅ IRIS instance already running"
else
    echo "Starting IRIS container..."
    docker-compose -f docker-compose.fraud.yml up -d

    echo "Waiting for IRIS to be healthy (30 seconds)..."
    sleep 30

    if docker ps | grep -q iris-fraud-mvp; then
        echo "✅ IRIS instance started successfully"
    else
        echo "❌ Failed to start IRIS instance"
        exit 1
    fi
fi

# Step 2: Export environment variables for fraud MVP
echo ""
echo "Step 2: Configuring environment variables..."
export IRIS_HOST=localhost
export IRIS_PORT=41972
export IRIS_NAMESPACE=USER
export IRIS_USER=_SYSTEM
export IRIS_PASSWORD=SYS

echo "✅ Environment configured (IRIS_PORT=41972)"

# Step 3: Load fraud schema
echo ""
echo "Step 3: Loading fraud schema (tables + stored procedures)..."
python scripts/fraud/load_fraud_schema.py

if [ $? -eq 0 ]; then
    echo "✅ Fraud schema loaded successfully"
else
    echo "❌ Failed to load fraud schema"
    exit 1
fi

# Step 4: Load sample data
echo ""
echo "Step 4: Loading sample fraud data (100 entities, 1000 events)..."
python scripts/fraud/load_sample_events.py

if [ $? -eq 0 ]; then
    echo "✅ Sample data loaded successfully"
else
    echo "❌ Failed to load sample data"
    exit 1
fi

# Step 5: Validate database setup
echo ""
echo "Step 5: Validating database setup..."
python -c "
import iris
import sys

try:
    conn = iris.connect('localhost', 41972, 'USER', '_SYSTEM', 'SYS')
    cursor = conn.cursor()

    cursor.execute('SELECT COUNT(*) FROM gs_events')
    num_events = cursor.fetchone()[0]

    cursor.execute('SELECT COUNT(*) FROM gs_labels')
    num_labels = cursor.fetchone()[0]

    conn.close()

    print(f'  ✅ gs_events: {num_events} rows')
    print(f'  ✅ gs_labels: {num_labels} rows')

    if num_events == 0 or num_labels == 0:
        print('  ⚠️  Warning: Some tables are empty')
        sys.exit(1)

except Exception as e:
    print(f'  ❌ Validation failed: {e}')
    sys.exit(1)
"

if [ $? -eq 0 ]; then
    echo "✅ Database validation passed"
else
    echo "❌ Database validation failed"
    exit 1
fi

# Step 6: Start FastAPI server (background)
echo ""
echo "Step 6: Starting FastAPI server (background)..."

# Check if server is already running
if curl -s http://localhost:8000/ > /dev/null 2>&1; then
    echo "✅ FastAPI server already running at http://localhost:8000"
else
    echo "Starting FastAPI server..."
    nohup uvicorn api.main:app --host 0.0.0.0 --port 8000 > /tmp/fraud-api.log 2>&1 &
    API_PID=$!

    echo "Waiting for API server to start (5 seconds)..."
    sleep 5

    if curl -s http://localhost:8000/ > /dev/null 2>&1; then
        echo "✅ FastAPI server started (PID: $API_PID)"
        echo "   Logs: tail -f /tmp/fraud-api.log"
    else
        echo "❌ Failed to start FastAPI server"
        echo "   Check logs: cat /tmp/fraud-api.log"
        exit 1
    fi
fi

# Step 7: Run MVP validation
echo ""
echo "Step 7: Running end-to-end MVP validation..."
python scripts/fraud/validate_mvp.py

if [ $? -eq 0 ]; then
    echo ""
    echo "=========================================="
    echo "✅ FRAUD MVP SETUP COMPLETE!"
    echo "=========================================="
    echo ""
    echo "Services:"
    echo "  - IRIS Database:        http://localhost:452773/csp/sys/UtilHome.csp"
    echo "  - IRIS SuperServer:     localhost:41972"
    echo "  - FastAPI Server:       http://localhost:8000"
    echo "  - API Documentation:    http://localhost:8000/docs"
    echo "  - Fraud Health:         http://localhost:8000/fraud/health"
    echo ""
    echo "Quick Test:"
    echo "  curl -X POST http://localhost:8000/fraud/score \\"
    echo "    -H 'Content-Type: application/json' \\"
    echo "    -d '{"
    echo "      \"mode\": \"MLP\","
    echo "      \"payer\": \"acct:sample_user001\","
    echo "      \"device\": \"dev:laptop\","
    echo "      \"ip\": \"ip:192.168.1.1\","
    echo "      \"merchant\": \"merchant:test\","
    echo "      \"amount\": 150.00"
    echo "    }'"
    echo ""
    echo "Next Steps:"
    echo "  1. Run tests: pytest tests/contract/test_fraud_score_contract.py -v"
    echo "  2. Run quickstart: python scripts/fraud/quickstart.py"
    echo "  3. Run benchmark: python scripts/fraud/benchmark_fraud_performance.py"
    echo ""
else
    echo ""
    echo "=========================================="
    echo "❌ FRAUD MVP VALIDATION FAILED"
    echo "=========================================="
    echo ""
    echo "Check the errors above and:"
    echo "  - View API logs: cat /tmp/fraud-api.log"
    echo "  - Check IRIS: docker logs iris-fraud-mvp"
    echo "  - Retry: ./scripts/fraud/setup_fraud_mvp.sh"
    exit 1
fi
