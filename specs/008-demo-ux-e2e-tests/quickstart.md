# Quickstart: E2E Demo Testing

**Feature**: 008-demo-ux-e2e-tests  
**Date**: 2025-01-18

## Prerequisites

1. **IRIS Database Running**
   ```bash
   # Option 1: Docker (recommended)
   docker-compose up -d
   
   # Option 2: ACORN-1 (pre-release with HNSW)
   docker-compose -f docker-compose.acorn.yml up -d
   ```

2. **Python Environment**
   ```bash
   uv sync
   source .venv/bin/activate
   ```

3. **Sample Data Loaded**
   ```bash
   # Load biomedical sample data
   python scripts/sample_data_768.sql
   
   # Load fraud detection sample data (new)
   python sql/fraud_sample_data.sql
   ```

## Running E2E Tests

### All E2E Tests
```bash
pytest tests/e2e/ -v --tb=short
```

### Biomedical Demo Tests Only
```bash
pytest tests/e2e/test_biomedical_demo.py -v
```

### Fraud Detection Demo Tests Only
```bash
pytest tests/e2e/test_fraud_demo.py -v
```

### With Performance Timing
```bash
pytest tests/e2e/ -v --durations=10
```

## Running Demo Scripts

### Biomedical Demo
```bash
python examples/demo_biomedical.py
```

Expected output:
```
IRIS Biomedical Demo
====================
[1/5] Connecting to database... OK (0.12s)
[2/5] Checking data availability... OK (42 proteins, 156 relationships)
[3/5] Vector similarity search... OK (5 results in 1.7ms)
[4/5] Graph traversal... OK (3 hops, 12 nodes)
[5/5] Hybrid search... OK (RRF fusion of 3 sources)

Demo completed successfully in 2.3s
```

### Fraud Detection Demo
```bash
python examples/demo_fraud_detection.py
```

Expected output:
```
IRIS Fraud Detection Demo
=========================
[1/6] Connecting to database... OK (0.11s)
[2/6] Loading fraud network... OK (75 accounts, 300 transactions)
[3/6] Ring pattern detection... OK (3 patterns found)
[4/6] Mule account detection... OK (2 high-degree nodes)
[5/6] Anomaly detection (vector)... OK (5 outliers)
[6/6] Alert summary... OK (25 alerts, 8 critical)

Demo completed successfully in 3.1s
```

## Troubleshooting

### Database Connection Failed
```
Error: Cannot connect to IRIS at localhost:1972
```
**Solution**:
1. Check if Docker container is running: `docker ps | grep iris`
2. If using ACORN-1, ensure port 21972: `docker ps | grep 21972`
3. Check .env file has correct IRIS_PORT setting

### Missing Sample Data
```
Error: No proteins found in database
```
**Solution**:
```bash
# For biomedical data
python -c "from scripts.setup import load_sample_data; load_sample_data()"

# For fraud data
python -c "from scripts.setup import load_fraud_data; load_fraud_data()"
```

### Vector Functions Unavailable
```
Warning: VECTOR_COSINE function not available
```
**Cause**: Using IRIS version < 2025.1 without Vector Search feature.
**Solution**: 
- Upgrade to IRIS 2025.1+ with Vector Search
- Or use ACORN-1 Docker image (includes HNSW)
- Demo will run with limited functionality (no vector similarity)

### Test Timeout
```
Error: Test exceeded 60s timeout
```
**Solution**:
- Check for slow queries: `pytest tests/e2e/ -v --durations=0`
- Ensure HNSW index exists for vector searches
- Run on faster hardware or increase timeout: `pytest --timeout=120`

## API Endpoints

After starting the API server (`uvicorn api.main:app --reload`):

| Endpoint | Description |
|----------|-------------|
| http://localhost:8000/graphql | GraphQL Playground |
| http://localhost:8000/docs | OpenAPI Documentation |
| http://localhost:8000/api/cypher | openCypher Endpoint |
| http://localhost:8000/health | Health Check |

## Test Markers

| Marker | Description |
|--------|-------------|
| `@pytest.mark.e2e` | End-to-end tests (full workflow) |
| `@pytest.mark.requires_database` | Tests requiring live IRIS |
| `@pytest.mark.slow` | Tests taking >10s |
| `@pytest.mark.integration` | Integration tests |
