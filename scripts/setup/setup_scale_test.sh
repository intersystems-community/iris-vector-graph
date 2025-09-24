#!/bin/bash

# Setup script for large-scale IRIS graph testing

set -e

echo "=== Setting up PMC Scale Test Environment ==="

# Check if IRIS container is running
if ! docker ps | grep -q iris_test_graph_ai; then
    echo "Starting IRIS test container..."
    cd /Users/tdyar/ws/graph-ai
    ./scripts/setup-test-env.sh
else
    echo "IRIS container already running"
fi

# Install required Python packages
echo "Installing Python dependencies..."
pip install numpy pyodbc lxml

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo "Creating .env file..."
    cp .env.sample .env
fi

# Verify IRIS connection
echo "Testing IRIS connection..."
python3 -c "
import pyodbc
try:
    conn = pyodbc.connect('DSN=IRIS_DEV;UID=_SYSTEM;PWD=SYS')
    print('✓ IRIS connection successful')
    conn.close()
except Exception as e:
    print(f'✗ IRIS connection failed: {e}')
    exit(1)
"

# Check PMC data availability
echo "Checking PMC data availability..."
PMC_SAMPLE_DIR="/Users/tdyar/ws/rag-templates/data/sample_10_docs"
if [ -d "$PMC_SAMPLE_DIR" ] && [ "$(ls -A $PMC_SAMPLE_DIR/*.xml 2>/dev/null)" ]; then
    PMC_COUNT=$(ls -1 $PMC_SAMPLE_DIR/*.xml | wc -l)
    echo "✓ Found $PMC_COUNT PMC sample documents"
else
    echo "✗ PMC sample documents not found at $PMC_SAMPLE_DIR"
    echo "Note: Will use synthetic biomedical data instead"
fi

# Clear any existing test data
echo "Clearing existing test data..."
docker exec iris_test_graph_ai /usr/irissys/bin/iris session IRIS -U %SYS << 'EOF'
&sql(DELETE FROM kg_Documents)
&sql(DELETE FROM kg_NodeEmbeddings)
&sql(DELETE FROM rdf_edges)
&sql(DELETE FROM rdf_props)
&sql(DELETE FROM rdf_labels)
write "Test data cleared", !
h
EOF

echo "=== Setup Complete ==="
echo ""
echo "To run the scale test:"
echo "cd /Users/tdyar/ws/graph-ai"
echo "python3 scripts/pmc_scale_test.py --max-docs 100 --workers 8"
echo ""
echo "For synthetic data test:"
echo "python3 scripts/scale_test.py --entities 50000 --edges 200000"