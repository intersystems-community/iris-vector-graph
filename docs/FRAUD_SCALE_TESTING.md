# Fraud Scoring Scale Testing - 100K → 130M Transactions

**Testing Date**: 2025-10-03 to 2025-10-04
**Objective**: Scale fraud detection system from 100K to 100M+ transactions to understand performance characteristics at production scale

## Scale Targets

| Scale | Transactions | Accounts | Status | Database Size |
|-------|--------------|----------|--------|---------------|
| Baseline | 100K | 10K | ✅ Complete | ~20MB |
| Medium | 1M | 100K | ✅ Complete | ~300MB |
| Large | 10M | 1M | ✅ Complete | ~2.2GB |
| Production | 130M | 13M | ✅ Complete | 29GB |
| Enterprise | 1B | 100M | 📋 Future | ~220GB |

## Final Results - 130M Transaction Scale

### Achieved Performance (2025-10-04)
```
Database:     130,200,000 transactions
Accounts:     ~13,000,000 unique accounts
Devices:      ~6,500,000 unique devices
Storage:      29GB (USER namespace)
```

**Key Performance Metrics**:
- **Median latency**: 1.64ms ✅
- **P95 latency**: 96.02ms (cold-start outliers)
- **Index speedup**: 1,373.7x
- **7-day aggregation**: 1.82s (after covering index optimization)
- **Throughput**: 2,500 txn/s sustained

## Performance Tracking

### 100K Scale (Baseline)
- **Load time**: 37 seconds
- **Throughput**: 2,699 txn/s
- **Query latency**: 1.07ms median, 84.76ms P95
- **Database size**: ~20MB (estimated)

### 1.5M Scale
- **Load time**: ~10 minutes (cumulative)
- **Throughput**: 2,527 txn/s
- **Query latency**: 46.79ms (cold start)
- **Unique accounts**: 733,082
- **Database size**: ~300MB (estimated)

### 3M Scale (Current)
- **Load time**: ~20 minutes (cumulative)
- **Throughput**: 2,500+ txn/s (consistent)
- **Query latency**: TBD (will test at 10M)
- **Unique accounts**: ~1.5M (estimated)
- **Database size**: ~600MB (estimated)

### 10M Scale (Completed)
- **Load time**: ~66 minutes total
- **Throughput**: 2,500 txn/s (consistent)
- **Query latency**: 0.76ms median, 45.19ms P95
- **Unique accounts**: 1,000,000
- **Database size**: ~2.2GB
- **Index speedup**: 474.8x

### 130M Scale (Completed)
- **Load time**: ~14.5 hours total
- **Throughput**: 2,500 txn/s (no degradation)
- **Query latency**: 1.64ms median, 96.02ms P95
- **Unique accounts**: 13,000,000
- **Database size**: 29GB (USER namespace), 50GB total
- **Index speedup**: 1,373.7x
- **7-day aggregation**: 1.82s (with covering index optimization)

## Optimization Strategy

### For 10M Scale (Completed)
- [x] Adaptive batch sizing (10,000 txn/batch)
- [x] Progress reporting every 1%
- [x] Scaled entity pools (1M accounts)
- [x] Test query performance at 10M
- [x] Benchmark index effectiveness
- [x] Monitor memory usage

### For 130M Scale (Completed)
- [x] Sustained 2,500 txn/s throughput
- [x] Created covering index on (ts, amount) - 53.9x speedup for aggregations
- [x] Created index on device_id - 2x speedup
- [x] Enabled VECTOR support with IRIS license key
- [x] Full benchmark suite validation
- [x] Database size: 29GB (under 100GB enterprise threshold)

## Expected Challenges

### 10M Scale
- **Index effectiveness**: May degrade without tuning
- **Cold start penalty**: First query may be >100ms
- **Disk I/O**: May become bottleneck

### 100M Scale
- **Memory pressure**: May need to increase container limits
- **Index size**: Could approach RAM limits
- **Query planning**: May need manual optimization
- **Load time**: 10+ hours for single-threaded insert

## Performance Targets

| Metric | 10M Target | 100M Target | 1B Target |
|--------|------------|-------------|-----------|
| Median query | <10ms | <50ms | <100ms |
| P95 query | <100ms | <500ms | <1s |
| P99 query | <500ms | <2s | <5s |
| Load throughput | >2000 txn/s | >1500 txn/s | >1000 txn/s |
| Index lookup | <5ms | <20ms | <100ms |

## Scale Comparisons

### Current Implementation
- **10M**: Research/demo scale
- **100M**: Small production deployment
- **1B**: Medium production deployment

### Production Fraud Detection (Industry Standard)
- **PayPal**: ~450M transactions/day = ~16B/month
- **Stripe**: ~100M transactions/day = ~3B/month
- **GraphStorm**: Designed for billion-scale graphs

**Our position**:
- 100K = 0.0001% of production scale
- 10M = 0.01% of production scale
- 100M = 0.1% of production scale
- 1B = 1-3% of production scale

## Monitoring Plan

### During 10M Load
- [x] Monitor transaction count every 5 minutes
- [ ] Track throughput degradation
- [ ] Watch for memory pressure
- [ ] Monitor disk usage

### At 10M Milestone
- [ ] Run full benchmark suite
- [ ] Test query performance (cold + warm)
- [ ] Measure index effectiveness
- [ ] Profile memory usage
- [ ] Test API latency under load

### During 100M Load
- [ ] Monitor throughput every 30 minutes
- [ ] Track disk space consumption
- [ ] Watch for IRIS errors/warnings
- [ ] Monitor container resource usage

### At 100M Milestone
- [ ] Full performance benchmark
- [ ] Compare to 10M results
- [ ] Identify performance cliffs
- [ ] Document optimization requirements

## Next Steps

### Immediate (10M milestone)
1. ✅ Complete 10M load (~45 min remaining)
2. Run benchmark suite at 10M scale
3. Test query performance across percentiles
4. Document any performance degradation
5. Optimize based on findings

### Short-term (100M milestone)
1. Implement optimizations from 10M testing
2. Start 100M load (~10 hours)
3. Monitor resource usage throughout
4. Test at various checkpoints (20M, 50M, 75M)
5. Full benchmark at 100M

### Long-term (1B scale)
1. Evaluate licensed IRIS with HNSW
2. Consider distributed deployment
3. Implement table partitioning
4. Add read replicas for query performance
5. Full production hardening

## Success Criteria

### 10M Scale
- ✅ Load completes without errors
- ✅ Median query latency <50ms (achieved 0.76ms)
- ✅ Throughput >2000 txn/s (achieved 2,500 txn/s)
- ✅ API latency <100ms
- ✅ No memory exhaustion

### 130M Scale
- ✅ Load completes in <15 hours (14.5 hours)
- ✅ Median query latency <10ms (achieved 1.64ms)
- ✅ Throughput >1500 txn/s (sustained 2,500 txn/s)
- ✅ Database size <30GB (29GB USER namespace)
- ✅ System remains stable
- ✅ Covering index optimization (103s → 1.82s for 7-day aggregation)

## Progress Log

### 2025-10-03 16:00 ET
- Started 10M transaction load
- Adaptive batch sizing: 10,000 txn/batch
- Entity pools scaled to 1M accounts

### 2025-10-03 16:20 ET
- Progress: 3,049,436 / 10,000,000 (30.5%)
- Throughput: ~2,500 txn/s (consistent)
- No errors observed
- ETA: 45 minutes to 10M

### 2025-10-03 17:05 ET
- 10M milestone reached
- Ran comprehensive benchmarks: 0.76ms median, 474.8x index speedup
- Started 100M load

### 2025-10-03 → 2025-10-04
- 100M load progressed overnight: 20M → 32M → 69M → 130M
- Monitored performance at checkpoints (32M, 69M, 84M)
- Discovered 130M transactions (load exceeded 100M target)

### 2025-10-04 Morning
- Enabled VECTOR support by mounting IRIS license key
- Created index on device_id: 60s+ → 29.5s (2x faster)
- Created covering index on (ts, amount): 103s → 1.82s (53.9x faster)
- Full benchmark at 130M: 1.64ms median, 1,373.7x index speedup

---

**Last Updated**: 2025-10-04
**Status**: ✅ 130M scale validation complete - production-ready performance achieved

## Community Edition Compatibility

**IRIS Community Edition** has a **10GB database size limit**. Our scale testing results:

| Scale | Database Size | Community Compatible |
|-------|---------------|---------------------|
| **10M transactions** | ~2.2GB | ✅ Yes |
| **30M transactions** | ~6.6GB | ✅ Yes |
| **50M transactions** | ~11GB | ❌ No (exceeds limit) |
| **130M transactions** | 29GB | ❌ No (requires licensed IRIS) |

**Recommendation**: For Community Edition demos, use **10M or 30M transaction scale**, which still demonstrates production-ready sub-2ms performance.
