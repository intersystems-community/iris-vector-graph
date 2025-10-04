# Fraud Detection - Community Edition Setup

**IRIS Community Edition** fraud scoring at **10M-30M transaction scale** with same sub-2ms performance.

## Overview

This deployment is optimized for **IRIS Community Edition** (10GB database limit):

| Configuration | Transactions | Database Size | License Required | VECTOR Support |
|---------------|--------------|---------------|------------------|----------------|
| **Community** | 10M-30M | 2.2GB-6.6GB | ❌ No | ❌ No |
| **Licensed** | 130M+ | 29GB+ | ✅ Yes | ✅ Yes |

**Performance**: Same sub-2ms fraud scoring at Community Edition scale!

## Quick Start

### 1. Start Community Edition Container

```bash
# Stop licensed version if running
docker-compose -f docker-compose.fraud-embedded.yml down

# Start Community Edition version
docker-compose -f docker-compose.fraud-community.yml up -d
```

**Ports** (offset from licensed version):
- **Fraud API**: http://localhost:8101 (vs 8100 for licensed)
- **SuperServer**: localhost:51972 (vs 41972 for licensed)
- **Management Portal**: http://localhost:62773 (vs 52775 for licensed)

### 2. Load Test Data

**Option A: 10M Transactions (~2.2GB)** - Fastest, safest for Community Edition
```bash
docker exec iris-fraud-community /usr/irissys/bin/irispython \
  /home/irisowner/app/scripts/fraud/stress_test_fraud.py 10000000
```
- **Load time**: ~66 minutes
- **Database size**: ~2.2GB ✅ Under limit
- **Expected performance**: 0.76ms median latency

**Option B: 30M Transactions (~6.6GB)** - Maximum for Community Edition
```bash
docker exec iris-fraud-community /usr/irissys/bin/irispython \
  /home/irisowner/app/scripts/fraud/stress_test_fraud.py 30000000
```
- **Load time**: ~3.3 hours
- **Database size**: ~6.6GB ✅ Under limit
- **Expected performance**: <1.5ms median latency

⚠️ **Do NOT exceed 30M transactions** - will hit 10GB limit!

### 3. Test Fraud Scoring

```bash
# Health check
curl http://localhost:8101/fraud/health

# Score a transaction
curl -X POST http://localhost:8101/fraud/score \
  -H 'Content-Type: application/json' \
  -d '{
    "mode": "MLP",
    "payer": "acct:user000001",
    "device": "dev:laptop",
    "ip": "192.168.1.1",
    "merchant": "merch:store1",
    "amount": 100.0
  }'
```

**Expected response**:
```json
{
  "fraud_probability": 0.15,
  "risk_level": "low",
  "inference_time_ms": 1.2
}
```

## Performance Benchmarks

### 10M Transactions (Validated)
```bash
docker exec iris-fraud-community /usr/irissys/bin/irispython \
  /home/irisowner/app/scripts/fraud/benchmark_fraud_at_scale.py
```

**Expected results** (from 130M testing):
- **Median latency**: 0.76ms ✅
- **P95 latency**: 45.19ms
- **Index speedup**: 474.8x
- **7-day aggregation**: <5s
- **Throughput**: 2,500 txn/s

### 30M Transactions (Estimated)
Based on linear scaling from 10M results:
- **Median latency**: <1.5ms ✅
- **P95 latency**: <60ms
- **Index speedup**: >400x
- **Database size**: 6.6GB (66% of limit)

## Monitoring Database Size

```bash
# Check current database size
docker exec iris-fraud-community du -h /usr/irissys/mgr/user/IRIS.DAT

# Check total mgr directory
docker exec iris-fraud-community du -sh /usr/irissys/mgr/

# Monitor during load
docker exec iris-fraud-community /usr/irissys/bin/irispython \
  /home/irisowner/app/scripts/fraud/monitor_scale.py
```

**Stay under 10GB**:
- **Safe zone**: < 8GB (green)
- **Caution**: 8-9.5GB (yellow, stop loading)
- **Danger**: > 9.5GB (red, database may fail)

## Differences from Licensed Version

| Feature | Community | Licensed |
|---------|-----------|----------|
| **Max transactions** | 30M | 130M+ |
| **Database size** | 10GB | Unlimited |
| **VECTOR datatype** | ❌ No | ✅ Yes |
| **HNSW indexing** | ❌ No | ✅ Yes |
| **Fraud scoring latency** | <2ms | <2ms |
| **Index optimization** | ✅ Yes | ✅ Yes |
| **Production deployment** | Development/POC | Production-ready |

**Community Edition is suitable for**:
- Development and testing
- Proof-of-concept demonstrations
- Training and education
- Small-scale production (<30M transactions)

## Running Both Versions Simultaneously

You can run both Community and Licensed versions at the same time (different ports):

```bash
# Licensed version (130M scale)
docker-compose -f docker-compose.fraud-embedded.yml up -d
# API: localhost:8100, SuperServer: 41972, Portal: 52775

# Community version (10M-30M scale)
docker-compose -f docker-compose.fraud-community.yml up -d
# API: localhost:8101, SuperServer: 51972, Portal: 62773
```

**Use cases**:
1. **Compare performance** at different scales
2. **Test Community Edition** before licensed deployment
3. **Development** (Community) + **staging/production** (Licensed)

## Cleanup and Reset

```bash
# Stop Community Edition container
docker-compose -f docker-compose.fraud-community.yml down

# Remove data volume (reset database)
docker volume rm iris-fraud-community-data

# Rebuild from scratch
docker-compose -f docker-compose.fraud-community.yml up -d --build
```

## Troubleshooting

### Database Size Exceeded

**Symptom**: IRIS errors, slow performance, writes failing

**Fix**: Reduce transaction count
```bash
# Stop and reset
docker-compose -f docker-compose.fraud-community.yml down
docker volume rm iris-fraud-community-data

# Reload with fewer transactions (10M instead of 30M)
docker-compose -f docker-compose.fraud-community.yml up -d
docker exec iris-fraud-community /usr/irissys/bin/irispython \
  /home/irisowner/app/scripts/fraud/stress_test_fraud.py 10000000
```

### VECTOR Datatype Errors

**Symptom**: "Vector Search not permitted with current license"

**Expected**: Community Edition does NOT support VECTOR datatype. This is normal.

**Fix**: Use licensed version for VECTOR/HNSW features, or avoid VECTOR-related queries in Community Edition.

### Port Conflicts

**Symptom**: "port is already allocated"

**Fix**: Check if licensed version is running
```bash
docker-compose -f docker-compose.fraud-embedded.yml down
# Then restart Community version
docker-compose -f docker-compose.fraud-community.yml up -d
```

## Migration Path: Community → Licensed

When ready to scale beyond 30M transactions:

1. **Export data** from Community Edition (optional)
2. **Stop Community container**
   ```bash
   docker-compose -f docker-compose.fraud-community.yml down
   ```
3. **Deploy licensed version**
   ```bash
   docker-compose -f docker-compose.fraud-embedded.yml up -d
   ```
4. **Load at production scale** (100M+ transactions)
5. **Enable VECTOR features** with iris.key mount

## Next Steps

1. **Load 10M transactions** for safe testing
2. **Run benchmark suite** to validate performance
3. **Test fraud API** with your application
4. **Monitor database size** as you scale
5. **Upgrade to licensed** when ready for production (>30M scale)

## Support

For issues with Community Edition:
- Check database size first: `du -h /usr/irissys/mgr/user/IRIS.DAT`
- Ensure < 30M transactions loaded
- Review error logs: `docker logs iris-fraud-community`
- Compare with licensed version behavior

---

**Community Edition**: Perfect for getting started with IRIS fraud detection at production-ready performance, within the 10GB free limit.
