#!/usr/bin/env python3
"""
Benchmark Fraud Scoring Performance at Scale

Tests performance with 100K+ transaction dataset to identify bottlenecks.
Must run via: /usr/irissys/bin/irispython
"""

import sys
import time
import statistics

try:
    import iris
except ImportError:
    print("ERROR: iris module not found. Run via /usr/irissys/bin/irispython")
    sys.exit(1)

print("="*60)
print("FRAUD SCORING PERFORMANCE BENCHMARK")
print("="*60)

# Verify data volume
result = iris.sql.exec("SELECT COUNT(*) FROM gs_events")
event_count = list(result)[0][0]
print(f"\nDatabase size: {event_count:,} transactions")

if event_count < 100000:
    print(f"❌ WARNING: Database has only {event_count:,} transactions")
    print("   Run stress_test_fraud.py first to load 100K+ transactions")
    sys.exit(1)

# Get sample accounts
result = iris.sql.exec("""
    SELECT DISTINCT entity_id FROM gs_events
    WHERE entity_id LIKE 'acct:user%'
""")
accounts = [row[0] for row in list(result)[:50]]
print(f"Testing with {len(accounts)} sample accounts\n")

# Benchmark 1: Feature computation latency
print("="*60)
print("BENCHMARK 1: Feature Computation Latency")
print("="*60)

latencies = []
for i, account in enumerate(accounts[:20], 1):
    start = time.perf_counter()

    # Feature 1 & 2: Rolling 24h
    result = iris.sql.exec("""
        SELECT COUNT(*) AS deg_24h, COALESCE(SUM(amount), 0.0) AS tx_amt_sum_24h
        FROM gs_events
        WHERE entity_id = ?
        AND ts >= DATEADD(hour, -24, CURRENT_TIMESTAMP)
    """, account)
    list(result)

    # Feature 3: Unique devices 7d
    result = iris.sql.exec("""
        SELECT COUNT(DISTINCT device_id) FROM gs_events
        WHERE entity_id = ?
        AND ts >= DATEADD(day, -7, CURRENT_TIMESTAMP)
        AND device_id IS NOT NULL
    """, account)
    list(result)

    elapsed = (time.perf_counter() - start) * 1000
    latencies.append(elapsed)

    if i % 5 == 0:
        print(f"  {i}/20 accounts tested, current: {elapsed:.2f}ms")

print(f"\nFeature Computation Statistics:")
print(f"  Min:    {min(latencies):.2f}ms")
print(f"  Median: {statistics.median(latencies):.2f}ms")
print(f"  P95:    {statistics.quantiles(latencies, n=20)[18]:.2f}ms")
print(f"  Max:    {max(latencies):.2f}ms")
print(f"  Mean:   {statistics.mean(latencies):.2f}ms")

# Benchmark 2: Query scalability
print("\n" + "="*60)
print("BENCHMARK 2: Query Scalability")
print("="*60)

# Test query performance with different time ranges
time_ranges = [
    ("1 hour", "DATEADD(hour, -1, CURRENT_TIMESTAMP)"),
    ("24 hours", "DATEADD(hour, -24, CURRENT_TIMESTAMP)"),
    ("7 days", "DATEADD(day, -7, CURRENT_TIMESTAMP)"),
    ("30 days", "DATEADD(day, -30, CURRENT_TIMESTAMP)"),
    ("90 days", "DATEADD(day, -90, CURRENT_TIMESTAMP)"),
]

test_account = accounts[0]
for range_name, sql_expr in time_ranges:
    start = time.perf_counter()
    result = iris.sql.exec(f"""
        SELECT COUNT(*), COALESCE(SUM(amount), 0.0)
        FROM gs_events
        WHERE entity_id = ?
        AND ts >= {sql_expr}
    """, test_account)
    rows = list(result)
    elapsed = (time.perf_counter() - start) * 1000

    count = rows[0][0] if rows else 0
    print(f"  {range_name:10} -> {count:3} txns in {elapsed:.2f}ms")

# Benchmark 3: Full table scan performance
print("\n" + "="*60)
print("BENCHMARK 3: Full Table Scan Performance")
print("="*60)

start = time.perf_counter()
result = iris.sql.exec("""
    SELECT COUNT(*), COALESCE(SUM(amount), 0.0), AVG(amount)
    FROM gs_events
    WHERE ts >= DATEADD(day, -7, CURRENT_TIMESTAMP)
""")
rows = list(result)
elapsed = (time.perf_counter() - start) * 1000

count = rows[0][0] if rows else 0
total = rows[0][1] if rows else 0.0
avg = rows[0][2] if rows else 0.0

print(f"  7-day aggregation: {count:,} txns, ${total:,.2f} total")
print(f"  Average amount: ${avg:.2f}")
print(f"  Query time: {elapsed:.2f}ms")
print(f"  Throughput: {count/elapsed*1000:.0f} rows/sec")

# Benchmark 4: Index effectiveness
print("\n" + "="*60)
print("BENCHMARK 4: Index Effectiveness")
print("="*60)

# Test indexed vs non-indexed queries
start = time.perf_counter()
result = iris.sql.exec("""
    SELECT COUNT(*) FROM gs_events
    WHERE entity_id = ?
""", test_account)
list(result)
indexed_time = (time.perf_counter() - start) * 1000

start = time.perf_counter()
result = iris.sql.exec("""
    SELECT COUNT(DISTINCT device_id) FROM gs_events
    WHERE device_id IS NOT NULL
""")
list(result)
full_scan_time = (time.perf_counter() - start) * 1000

print(f"  Indexed query (entity_id):     {indexed_time:.2f}ms")
print(f"  Full scan (device_id):         {full_scan_time:.2f}ms")
print(f"  Index speedup:                 {full_scan_time/indexed_time:.1f}x")

print("\n" + "="*60)
print("BENCHMARK COMPLETE")
print("="*60)

# Performance targets
print("\n✅ Performance Targets:")
print(f"  Feature query <10ms:           {'✅ PASS' if statistics.median(latencies) < 10 else '❌ FAIL'}")
print(f"  P95 latency <20ms:             {'✅ PASS' if statistics.quantiles(latencies, n=20)[18] < 20 else '❌ FAIL'}")
print(f"  Full scan <100ms:              {'✅ PASS' if elapsed < 100 else '❌ FAIL'}")
