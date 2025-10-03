#!/usr/bin/env python3
"""
Fraud Scoring MVP Validation Script

This script validates the complete MVP implementation end-to-end:
1. Database connectivity and schema validation
2. Sample data availability
3. FastAPI server health
4. API endpoint functionality
5. Performance targets

Run this to validate the MVP is ready for production.
"""

import os
import sys
import requests
import time
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

def print_section(title):
    print("\n" + "="*70)
    print(f" {title}")
    print("="*70)

def check_database_connection():
    """Step 1: Verify IRIS database connection"""
    print_section("Step 1: Database Connection")

    try:
        import iris

        # Use positional arguments (not keyword) for iris.connect()
        conn = iris.connect(
            os.getenv("IRIS_HOST", "localhost"),
            int(os.getenv("IRIS_PORT", "41972")),
            os.getenv("IRIS_NAMESPACE", "USER"),
            os.getenv("IRIS_USER", "_SYSTEM"),
            os.getenv("IRIS_PASSWORD", "SYS")
        )

        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()

        assert result[0] == 1
        print("✅ IRIS database connection successful")

        conn.close()
        return True

    except Exception as e:
        print(f"❌ Database connection failed: {e}")
        print("\n💡 Solutions:")
        print("   - Check IRIS is running: docker ps | grep iris")
        print("   - Start IRIS: docker-compose up -d")
        print("   - Check .env file has correct IRIS_* settings")
        return False

def check_fraud_schema():
    """Step 2: Verify fraud schema is loaded"""
    print_section("Step 2: Fraud Schema Validation")

    try:
        import iris

        # Use positional arguments (not keyword) for iris.connect()
        conn = iris.connect(
            os.getenv("IRIS_HOST", "localhost"),
            int(os.getenv("IRIS_PORT", "41972")),
            os.getenv("IRIS_NAMESPACE", "USER"),
            os.getenv("IRIS_USER", "_SYSTEM"),
            os.getenv("IRIS_PASSWORD", "SYS")
        )

        cursor = conn.cursor()

        # Check tables
        tables = ['gs_events', 'gs_labels', 'gs_fraud_centroid']
        all_tables_exist = True

        for table in tables:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                print(f"  ✅ Table {table}: {count} rows")
            except Exception as e:
                print(f"  ❌ Table {table} missing: {e}")
                all_tables_exist = False

        conn.close()

        if not all_tables_exist:
            print("\n💡 Load the fraud schema:")
            print("   python scripts/fraud/load_fraud_schema.py")
            return False

        print("✅ Fraud schema loaded")
        return True

    except Exception as e:
        print(f"❌ Schema validation failed: {e}")
        return False

def check_sample_data():
    """Step 3: Verify sample data exists"""
    print_section("Step 3: Sample Data Validation")

    try:
        import iris

        # Use positional arguments (not keyword) for iris.connect()
        conn = iris.connect(
            os.getenv("IRIS_HOST", "localhost"),
            int(os.getenv("IRIS_PORT", "41972")),
            os.getenv("IRIS_NAMESPACE", "USER"),
            os.getenv("IRIS_USER", "_SYSTEM"),
            os.getenv("IRIS_PASSWORD", "SYS")
        )

        cursor = conn.cursor()

        # Check events
        cursor.execute("SELECT COUNT(*) FROM gs_events")
        num_events = cursor.fetchone()[0]

        if num_events == 0:
            print(f"  ⚠️  No events found in gs_events")
            print("\n💡 Load sample data:")
            print("   python scripts/fraud/load_sample_events.py")
            conn.close()
            return False

        print(f"  ✅ gs_events: {num_events} events")

        # Check labels
        cursor.execute("SELECT COUNT(*) FROM gs_labels WHERE label = 'fraud'")
        num_fraud = cursor.fetchone()[0]
        print(f"  ✅ gs_labels: {num_fraud} fraud labels")

        # Check centroid
        cursor.execute("SELECT num_fraud_nodes FROM gs_fraud_centroid WHERE centroid_id = 1")
        result = cursor.fetchone()

        if result:
            print(f"  ✅ gs_fraud_centroid: computed from {result[0]} fraud nodes")
        else:
            print(f"  ⚠️  gs_fraud_centroid: no centroid found")

        conn.close()
        print("✅ Sample data loaded")
        return True

    except Exception as e:
        print(f"❌ Sample data validation failed: {e}")
        return False

def check_api_server():
    """Step 4: Verify FastAPI server is running"""
    print_section("Step 4: API Server Health Check")

    api_url = "http://localhost:8000"

    try:
        # Check root endpoint
        response = requests.get(f"{api_url}/", timeout=5.0)
        response.raise_for_status()
        print(f"  ✅ API server responding at {api_url}")

        # Check fraud health endpoint
        response = requests.get(f"{api_url}/fraud/health", timeout=5.0)
        response.raise_for_status()

        health_data = response.json()
        print(f"  ✅ Fraud health endpoint: {health_data['status']}")
        print(f"     - Model loaded: {health_data.get('model_loaded', 'N/A')}")
        print(f"     - Database connected: {health_data.get('database_connected', 'N/A')}")
        print(f"     - Centroid available: {health_data.get('centroid_available', 'N/A')}")

        return True

    except requests.exceptions.ConnectionError:
        print(f"  ❌ API server not running at {api_url}")
        print("\n💡 Start the FastAPI server:")
        print("   uvicorn api.main:app --reload")
        return False
    except Exception as e:
        print(f"  ❌ API health check failed: {e}")
        return False

def test_fraud_scoring():
    """Step 5: Test fraud scoring endpoint"""
    print_section("Step 5: Fraud Scoring Endpoint Test")

    api_url = "http://localhost:8000/fraud/score"

    # Test payload
    payload = {
        "mode": "MLP",
        "payer": "acct:sample_user001",
        "device": "dev:laptop",
        "ip": "ip:192.168.1.1",
        "merchant": "merchant:test_merchant",
        "amount": 150.00,
        "country": "US"
    }

    try:
        print(f"  Sending POST {api_url}")

        start_time = time.perf_counter()
        response = requests.post(api_url, json=payload, timeout=5.0)
        end_time = time.perf_counter()

        latency_ms = (end_time - start_time) * 1000

        if response.status_code == 200:
            data = response.json()

            print(f"  ✅ Response status: 200 OK")
            print(f"  ✅ Latency: {latency_ms:.2f}ms")

            # Validate response structure
            assert "prob" in data, "Missing 'prob' field"
            assert "reasons" in data, "Missing 'reasons' field"
            assert "trace_id" in data, "Missing 'trace_id' field"

            prob = data["prob"]
            reasons = data["reasons"]

            print(f"  ✅ Fraud probability: {prob:.4f}")
            print(f"  ✅ Reason codes: {len(reasons)} reasons")

            # Check min 3 reasons (FR-002)
            if len(reasons) >= 3:
                print(f"  ✅ Min 3 reason codes requirement met")
            else:
                print(f"  ⚠️  Only {len(reasons)} reason codes (expected ≥3)")

            # Check latency SLO (<20ms p95)
            if latency_ms < 20.0:
                print(f"  ✅ Latency meets <20ms SLO")
            else:
                print(f"  ⚠️  Latency {latency_ms:.2f}ms exceeds 20ms SLO")

            # Print reason codes
            print(f"\n  Top Reasons:")
            for i, reason in enumerate(reasons[:5], 1):
                print(f"    {i}. {reason}")

            print("\n✅ Fraud scoring endpoint working")
            return True

        else:
            print(f"  ❌ Response status: {response.status_code}")
            print(f"  Response: {response.text}")
            return False

    except Exception as e:
        print(f"  ❌ Fraud scoring test failed: {e}")
        return False

def main():
    """Run complete MVP validation"""
    print("="*70)
    print(" Fraud Scoring MVP Validation")
    print("="*70)
    print(f"Timestamp: {datetime.now().isoformat()}")

    results = {
        "Database Connection": check_database_connection(),
        "Fraud Schema": False,
        "Sample Data": False,
        "API Server": False,
        "Fraud Scoring": False
    }

    # Only proceed with schema check if DB is connected
    if results["Database Connection"]:
        results["Fraud Schema"] = check_fraud_schema()

    # Only check sample data if schema exists
    if results["Fraud Schema"]:
        results["Sample Data"] = check_sample_data()

    # Check API server (independent of database)
    results["API Server"] = check_api_server()

    # Only test fraud scoring if everything is ready
    if results["API Server"] and results["Sample Data"]:
        results["Fraud Scoring"] = test_fraud_scoring()

    # Print summary
    print_section("Validation Summary")

    for check, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status}: {check}")

    all_passed = all(results.values())

    if all_passed:
        print("\n" + "="*70)
        print("✅ MVP VALIDATION PASSED - System is production-ready!")
        print("="*70)
        print("\nNext steps:")
        print("  1. Run contract tests: pytest tests/contract/test_fraud_score_contract.py -v")
        print("  2. Run integration tests: pytest tests/integration/ -v")
        print("  3. Run performance benchmark: python scripts/fraud/benchmark_fraud_performance.py")
        print("\nAPI Documentation: http://localhost:8000/docs")
        return 0
    else:
        print("\n" + "="*70)
        print("❌ MVP VALIDATION FAILED - See errors above")
        print("="*70)

        # Provide next steps based on what failed
        if not results["Database Connection"]:
            print("\n💡 Start IRIS database:")
            print("   docker-compose up -d")
        elif not results["Fraud Schema"]:
            print("\n💡 Load fraud schema:")
            print("   python scripts/fraud/load_fraud_schema.py")
        elif not results["Sample Data"]:
            print("\n💡 Load sample data:")
            print("   python scripts/fraud/load_sample_events.py")
        elif not results["API Server"]:
            print("\n💡 Start FastAPI server:")
            print("   uvicorn api.main:app --reload")

        return 1

if __name__ == "__main__":
    sys.exit(main())
