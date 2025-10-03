#!/usr/bin/env python3
"""
Stress Test Fraud Scoring System at Scale

Generates realistic data volumes to test:
- Configurable from 100K to 100M+ transactions
- Scalable entity pools (accounts, devices, merchants)
- Graph edges for risk propagation
- Performance metrics at scale

Must run via: /usr/irissys/bin/irispython

Usage:
    /usr/irissys/bin/irispython stress_test_fraud.py [num_transactions]

Examples:
    stress_test_fraud.py             # Default: 100K
    stress_test_fraud.py 10000000    # 10M transactions
    stress_test_fraud.py 100000000   # 100M transactions
"""

import sys
import time
import random
from datetime import datetime, timedelta

try:
    import iris
except ImportError:
    print("ERROR: iris module not found. Run via /usr/irissys/bin/irispython")
    sys.exit(1)

# Parse command line arguments
if len(sys.argv) > 1:
    try:
        NUM_TRANSACTIONS = int(sys.argv[1])
    except ValueError:
        print(f"ERROR: Invalid transaction count: {sys.argv[1]}")
        sys.exit(1)
else:
    NUM_TRANSACTIONS = 100000

print("="*60)
print("FRAUD SCORING STRESS TEST")
print("="*60)

# Scale entity pools based on transaction volume
# Rule of thumb: ~10 transactions per account on average
NUM_ACCOUNTS = max(10000, NUM_TRANSACTIONS // 10)
NUM_DEVICES = max(5000, NUM_ACCOUNTS // 2)
NUM_MERCHANTS = max(1000, NUM_ACCOUNTS // 10)
NUM_IPS = max(2000, NUM_ACCOUNTS // 5)
FRAUD_RATE = 0.05  # 5% fraud rate

# Adaptive batch size - larger batches for bigger datasets
if NUM_TRANSACTIONS <= 100000:
    BATCH_SIZE = 1000
elif NUM_TRANSACTIONS <= 1000000:
    BATCH_SIZE = 5000
elif NUM_TRANSACTIONS <= 10000000:
    BATCH_SIZE = 10000
else:
    BATCH_SIZE = 50000

print(f"\nTarget volumes:")
print(f"  Accounts:     {NUM_ACCOUNTS:,}")
print(f"  Devices:      {NUM_DEVICES:,}")
print(f"  Merchants:    {NUM_MERCHANTS:,}")
print(f"  IPs:          {NUM_IPS:,}")
print(f"  Transactions: {NUM_TRANSACTIONS:,}")
print(f"  Fraud rate:   {FRAUD_RATE*100:.1f}%")
print()

# Generate entity pools
print("Generating entity pools...")
start = time.time()

accounts = [f"acct:user{i:06d}" for i in range(NUM_ACCOUNTS)]
devices = [f"dev:device{i:05d}" for i in range(NUM_DEVICES)]
merchants = [f"merch:merchant{i:04d}" for i in range(NUM_MERCHANTS)]
ips = [f"ip:10.{i//256}.{i%256}.{random.randint(1,254)}" for i in range(NUM_IPS)]

print(f"✅ Generated entity pools in {time.time()-start:.2f}s\n")

# Insert transactions in batches
print(f"\nInserting {NUM_TRANSACTIONS:,} transactions...")
print(f"Batch size: {BATCH_SIZE:,}")
batch_count = 0
start = time.time()
total_inserted = 0
errors = 0

# Calculate reporting frequency (every 1% or every 10 batches, whichever is less frequent)
report_every = max(10, (NUM_TRANSACTIONS // BATCH_SIZE) // 100)

for batch_num in range(0, NUM_TRANSACTIONS, BATCH_SIZE):
    batch_start = time.perf_counter()
    batch_inserted = 0

    for i in range(BATCH_SIZE):
        if batch_num + i >= NUM_TRANSACTIONS:
            break

        # Random entity selection
        payer = random.choice(accounts)
        device = random.choice(devices) if random.random() > 0.1 else None
        merchant = random.choice(merchants)
        ip = random.choice(ips) if random.random() > 0.1 else None

        # Amount distribution (log-normal, mostly small transactions)
        amount = random.lognormvariate(4.5, 1.2)  # Mean ~$100, variance high

        # Timestamp (distributed over last 90 days)
        # Use SQL DATEADD instead of Python datetime parameters
        days_offset = random.randint(-90, 0)
        hours_offset = random.randint(0, 23)
        minutes_offset = random.randint(0, 59)

        try:
            iris.sql.exec("""
                INSERT INTO gs_events (entity_id, kind, ts, amount, device_id, ip, metadata)
                VALUES (?, 'tx',
                    DATEADD(minute, ?, DATEADD(hour, ?, DATEADD(day, ?, CURRENT_TIMESTAMP))),
                    ?, ?, ?, ?)
            """, payer, minutes_offset, hours_offset, days_offset, amount, device, ip,
                 f'{{"merchant":"{merchant}"}}')
            batch_inserted += 1
        except Exception as e:
            errors += 1
            if errors <= 10:  # Only print first 10 errors
                print(f"Error inserting transaction: {e}")
            if errors == 10:
                print("  (suppressing further error messages...)")

    batch_count += 1
    total_inserted += batch_inserted
    batch_time = time.perf_counter() - batch_start
    total_time = time.time() - start

    # Progress reporting
    if batch_count % report_every == 0 or batch_num + BATCH_SIZE >= NUM_TRANSACTIONS:
        rate = total_inserted / total_time
        eta_seconds = (NUM_TRANSACTIONS - total_inserted) / rate if rate > 0 else 0
        eta_minutes = eta_seconds / 60
        percent = (total_inserted / NUM_TRANSACTIONS) * 100

        print(f"  {total_inserted:,} / {NUM_TRANSACTIONS:,} ({percent:.1f}%) | "
              f"{rate:.0f} txn/s | batch: {batch_time:.2f}s | ETA: {eta_minutes:.1f}m")

total_time = time.time() - start
total_minutes = total_time / 60
print(f"\n✅ Inserted {total_inserted:,} transactions in {total_minutes:.1f} minutes")
print(f"   Throughput: {total_inserted/total_time:.0f} txn/s")
if errors > 0:
    print(f"   ⚠️  Errors: {errors}")
print()

# Generate fraud labels
print(f"Generating fraud labels ({FRAUD_RATE*100:.1f}% fraud rate)...")
start = time.time()

num_fraud = int(NUM_ACCOUNTS * FRAUD_RATE)
fraud_accounts = random.sample(accounts, num_fraud)

for account in fraud_accounts:
    # Label timestamp (recent discovery)
    days_ago = random.randint(0, 30)

    try:
        iris.sql.exec("""
            INSERT INTO gs_labels (entity_id, label, label_ts, source)
            VALUES (?, 1, DATEADD(day, ?, CURRENT_TIMESTAMP), 'synthetic_test')
        """, account, -days_ago)
    except Exception as e:
        print(f"Error inserting label: {e}")

print(f"✅ Inserted {num_fraud:,} fraud labels in {time.time()-start:.2f}s\n")

# Verify data volumes
print("="*60)
print("VERIFICATION")
print("="*60)

result = iris.sql.exec("SELECT COUNT(*) FROM gs_events")
event_count = list(result)[0][0]
print(f"Events in database:     {event_count:,}")

result = iris.sql.exec("SELECT COUNT(*) FROM gs_labels")
label_count = list(result)[0][0]
print(f"Labels in database:     {label_count:,}")

result = iris.sql.exec("SELECT COUNT(DISTINCT entity_id) FROM gs_events")
unique_accounts = list(result)[0][0]
print(f"Unique accounts:        {unique_accounts:,}")

result = iris.sql.exec("SELECT COUNT(DISTINCT device_id) FROM gs_events WHERE device_id IS NOT NULL")
unique_devices = list(result)[0][0]
print(f"Unique devices:         {unique_devices:,}")

result = iris.sql.exec("SELECT MIN(amount), MAX(amount), AVG(amount) FROM gs_events WHERE amount IS NOT NULL")
min_amt, max_amt, avg_amt = list(result)[0]
print(f"Amount range:           ${min_amt:.2f} - ${max_amt:.2f} (avg: ${avg_amt:.2f})")

# Performance test
print("\n" + "="*60)
print("PERFORMANCE TEST")
print("="*60)

test_account = random.choice(accounts)
print(f"\nTesting fraud scoring for: {test_account}")

# Time feature computation
start = time.time()
result = iris.sql.exec("""
    SELECT COUNT(*) AS deg_24h, COALESCE(SUM(amount), 0.0) AS tx_amt_sum_24h
    FROM gs_events
    WHERE entity_id = ?
    AND ts >= DATEADD(hour, -24, CURRENT_TIMESTAMP)
""", test_account)
rows = list(result)
deg_24h, tx_amt_sum_24h = rows[0]
feature_time = (time.time() - start) * 1000

print(f"  24h transaction count: {deg_24h}")
print(f"  24h transaction sum:   ${tx_amt_sum_24h:.2f}")
print(f"  Feature query time:    {feature_time:.2f}ms")

# Test batch query performance
print(f"\nBatch query test (10 accounts):")
test_accounts = random.sample(accounts, 10)

start = time.time()
for acc in test_accounts:
    result = iris.sql.exec("""
        SELECT COUNT(*), COALESCE(SUM(amount), 0.0)
        FROM gs_events
        WHERE entity_id = ?
        AND ts >= DATEADD(hour, -24, CURRENT_TIMESTAMP)
    """, acc)
    list(result)  # Consume results

batch_time = (time.time() - start) * 1000
print(f"  Total time: {batch_time:.2f}ms")
print(f"  Per account: {batch_time/10:.2f}ms")

print("\n" + "="*60)
print("STRESS TEST COMPLETE")
print("="*60)
print(f"\n✅ Database loaded with {event_count:,} transactions")
print(f"✅ Ready for scale testing and performance analysis")
print(f"\nTo test fraud scoring:")
print(f"  curl -X POST http://localhost:8100/fraud/score \\")
print(f"    -H 'Content-Type: application/json' \\")
print(f"    -d '{{\"mode\":\"MLP\",\"payer\":\"{test_account}\",\"device\":\"dev:laptop\",")
print(f"         \"ip\":\"ip:192.168.1.1\",\"merchant\":\"merch:store1\",\"amount\":100.0}}'")
