#!/usr/bin/env python3
"""
Diagnose Performance Issues at Scale

Identifies bottlenecks and outliers in fraud scoring queries.
Must run via: /usr/irissys/bin/irispython
"""

import sys
import time

try:
    import iris
except ImportError:
    print("ERROR: iris module not found. Run via /usr/irissys/bin/irispython")
    sys.exit(1)

print("="*60)
print("FRAUD QUERY PERFORMANCE DIAGNOSTICS")
print("="*60)

# Get sample accounts
result = iris.sql.exec("SELECT DISTINCT entity_id FROM gs_events WHERE entity_id LIKE 'acct:user%'")
accounts = [row[0] for row in list(result)[:50]]

# Test each account and find outliers
print(f"\nTesting {len(accounts)} accounts for outliers...\n")

timings = []
for account in accounts:
    start = time.perf_counter()
    result = iris.sql.exec("""
        SELECT COUNT(*) AS deg_24h, COALESCE(SUM(amount), 0.0) AS tx_amt_sum_24h
        FROM gs_events
        WHERE entity_id = ?
        AND ts >= DATEADD(hour, -24, CURRENT_TIMESTAMP)
    """, account)
    rows = list(result)
    elapsed = (time.perf_counter() - start) * 1000

    count = rows[0][0] if rows else 0
    timings.append((account, elapsed, count))

# Sort by elapsed time
timings.sort(key=lambda x: x[1], reverse=True)

print("SLOWEST QUERIES:")
print("-" * 60)
for i, (account, elapsed, count) in enumerate(timings[:10], 1):
    print(f"{i:2}. {account:20} -> {elapsed:6.2f}ms ({count} txns in 24h)")

print("\nFASTEST QUERIES:")
print("-" * 60)
for i, (account, elapsed, count) in enumerate(timings[-10:], 1):
    print(f"{i:2}. {account:20} -> {elapsed:6.2f}ms ({count} txns in 24h)")

# Check if slow accounts have more historical data
print("\n" + "="*60)
print("CORRELATION: Query Time vs Total Transaction Count")
print("="*60)

slow_accounts = [t[0] for t in timings[:5]]
fast_accounts = [t[0] for t in timings[-5:]]

print("\nSLOW accounts (total transaction volume):")
for account in slow_accounts:
    result = iris.sql.exec("SELECT COUNT(*) FROM gs_events WHERE entity_id = ?", account)
    total = list(result)[0][0]
    print(f"  {account}: {total} total transactions")

print("\nFAST accounts (total transaction volume):")
for account in fast_accounts:
    result = iris.sql.exec("SELECT COUNT(*) FROM gs_events WHERE entity_id = ?", account)
    total = list(result)[0][0]
    print(f"  {account}: {total} total transactions")

# Test index usage
print("\n" + "="*60)
print("INDEX ANALYSIS")
print("="*60)

# Check if idx_gs_events_entity_ts exists and is being used
result = iris.sql.exec("""
    SELECT COUNT(*) FROM gs_events
    WHERE entity_id = ?
""", timings[0][0])
slow_account_total = list(result)[0][0]

print(f"\nTest account: {timings[0][0]}")
print(f"Total transactions: {slow_account_total}")

# Time WITH index hint
start = time.perf_counter()
result = iris.sql.exec("""
    SELECT COUNT(*), COALESCE(SUM(amount), 0.0)
    FROM gs_events
    WHERE entity_id = ?
    AND ts >= DATEADD(hour, -24, CURRENT_TIMESTAMP)
""", timings[0][0])
list(result)
with_index_time = (time.perf_counter() - start) * 1000

print(f"Query time: {with_index_time:.2f}ms")

# Check timestamp distribution
print("\n" + "="*60)
print("TIMESTAMP DISTRIBUTION")
print("="*60)

result = iris.sql.exec("SELECT MIN(ts), MAX(ts) FROM gs_events")
rows = list(result)
print(f"Min timestamp: {rows[0][0]}")
print(f"Max timestamp: {rows[0][1]}")

# Count by day
result = iris.sql.exec("""
    SELECT
        CASE
            WHEN ts >= DATEADD(day, -1, CURRENT_TIMESTAMP) THEN '0-1 days'
            WHEN ts >= DATEADD(day, -7, CURRENT_TIMESTAMP) THEN '1-7 days'
            WHEN ts >= DATEADD(day, -30, CURRENT_TIMESTAMP) THEN '7-30 days'
            WHEN ts >= DATEADD(day, -90, CURRENT_TIMESTAMP) THEN '30-90 days'
            ELSE '90+ days'
        END AS age_bucket,
        COUNT(*) AS count
    FROM gs_events
    GROUP BY age_bucket
""")

print("\nTransaction age distribution:")
for row in result:
    bucket, count = row
    print(f"  {bucket:15} -> {count:,} transactions")

print("\n" + "="*60)
print("DIAGNOSTICS COMPLETE")
print("="*60)
