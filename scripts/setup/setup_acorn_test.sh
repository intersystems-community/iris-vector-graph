#!/bin/bash

# Setup script for ACORN-1 optimized IRIS testing
set -e

echo "=== Setting up ACORN-1 Optimized IRIS Environment ==="

# Check if we have access to the internal Docker registry
echo "Checking access to IRIS 2025.3.0EHAT.127.0 with ACORN-1..."

# Stop any existing IRIS containers
echo "Stopping existing IRIS containers..."
docker stop iris_test_graph_ai 2>/dev/null || true
docker stop iris_acorn_graph_ai 2>/dev/null || true

# Start ACORN-1 optimized IRIS
echo "Starting IRIS 2025.3.0EHAT.127.0 with ACORN-1 optimization..."
cd /Users/tdyar/ws/graph-ai

# Try to pull the ACORN-1 image
if docker pull docker.iscinternal.com/intersystems/iris-lockeddown:2025.3.0EHAT.127.0-linux-arm64v8; then
    echo "✓ Successfully pulled ACORN-1 optimized IRIS image"

    # Start with the new image
    docker-compose -f docker-compose.acorn.yml up -d

    # Wait for health check
    echo "Waiting for IRIS to be healthy..."
    timeout=300
    counter=0
    while [ $counter -lt $timeout ]; do
        if docker ps --format "table {{.Names}}\t{{.Status}}" | grep iris_acorn_graph_ai | grep -q "healthy"; then
            echo "✓ IRIS ACORN-1 container is healthy"
            break
        fi
        echo "Waiting for IRIS to be healthy... ($counter/$timeout)"
        sleep 5
        counter=$((counter + 5))
    done

    if [ $counter -ge $timeout ]; then
        echo "✗ IRIS container failed to become healthy within $timeout seconds"
        docker logs iris_acorn_graph_ai
        exit 1
    fi

    # Update .env to point to ACORN-1 container
    echo "Updating environment configuration..."
    sed -i.bak 's/IRIS_PORT=1973/IRIS_PORT=1973/' .env
    echo "IRIS_VERSION=2025.3.0EHAT.127.0-ACORN1" >> .env

    echo "✓ ACORN-1 IRIS environment ready!"
    echo ""
    echo "ACORN-1 Features Available:"
    echo "- Optimized HNSW index building with ACORN-1 algorithm"
    echo "- Faster vector similarity search"
    echo "- Enhanced performance for 768D embeddings"
    echo ""
    echo "To test ACORN-1 performance:"
    echo "python3 scripts/string_db_scale_test.py --max-proteins 10000 --workers 8"

else
    echo "⚠️  ACORN-1 IRIS image not available"
    echo "Falling back to Community Edition..."

    # Fallback to original setup
    ./scripts/setup-test-env.sh
    echo "Note: ACORN-1 optimization requires access to internal IRIS builds"
fi

echo "=== Setup Complete ==="