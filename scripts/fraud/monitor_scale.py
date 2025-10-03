#!/usr/bin/env python3
"""
Monitor Fraud Database Scale

Real-time monitoring of database size and performance as it scales.
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
print("FRAUD DATABASE SCALE MONITOR")
print("="*60)

# Get current database size
result = iris.sql.exec("SELECT COUNT(*) FROM gs_events")
event_count = list(result)[0][0]

result = iris.sql.exec("SELECT COUNT(DISTINCT entity_id) FROM gs_events")
unique_accounts = list(result)[0][0]

result = iris.sql.exec("SELECT COUNT(*) FROM gs_labels")
label_count = list(result)[0][0]

print(f"\nCurrent Scale:")
print(f"  Transactions:    {event_count:,}")
print(f"  Unique Accounts: {unique_accounts:,}")
print(f"  Fraud Labels:    {label_count:,}")

# Test query performance at current scale
print(f"\n" + "="*60)
print("PERFORMANCE TEST AT CURRENT SCALE")
print("="*60)

# Get sample account with transactions
result = iris.sql.exec("""
    SELECT entity_id, COUNT(*) as tx_count
    FROM gs_events
    GROUP BY entity_id
    ORDER BY tx_count DESC
""")
rows = list(result)
if rows:
    test_account = rows[0][0]
    account_txs = rows[0][1]
    print(f"\nTest account: {test_account} ({account_txs} total transactions)")

    # Time feature query
    start = time.perf_counter()
    result = iris.sql.exec("""
        SELECT COUNT(*) AS deg_24h, COALESCE(SUM(amount), 0.0) AS tx_amt_sum_24h
        FROM gs_events
        WHERE entity_id = ?
        AND ts >= DATEADD(hour, -24, CURRENT_TIMESTAMP)
    """, test_account)
    rows = list(result)
    elapsed_ms = (time.perf_counter() - start) * 1000

    deg_24h = rows[0][0] if rows else 0
    tx_amt_sum_24h = rows[0][1] if rows else 0.0

    print(f"  24h transactions: {deg_24h}")
    print(f"  24h amount:       ${tx_amt_sum_24h:.2f}")
    print(f"  Query latency:    {elapsed_ms:.2f}ms")
else:
    print("\n❌ No transactions found")

# Performance projections
print(f"\n" + "="*60)
print("SCALE PROJECTIONS")
print("="*60)

current_scale = event_count
targets = [
    (1_000_000, "1M"),
    (10_000_000, "10M"),
    (100_000_000, "100M"),
    (1_000_000_000, "1B")
]

print(f"\nFrom current {event_count:,} transactions:")
for target, label in targets:
    if target > current_scale:
        multiplier = target / current_scale
        print(f"  → {label:>4}: {multiplier:.1f}x scale-up needed")

print("\n" + "="*60)
print("MONITOR COMPLETE")
print("="*60)
