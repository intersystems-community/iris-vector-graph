#!/usr/bin/env python3
"""
Complete IRIS Graph-AI Validation Runner

This script runs all validation tests and demonstrations to ensure
that the IRIS Graph-AI system is working correctly and that all
documentation is accurate.

It runs:
1. Schema validation tests
2. Vector function tests
3. SQL pattern tests
4. End-to-end workflow demonstration

Run this after setting up the test environment with:
  ./scripts/setup/setup-test-env.sh
"""

import os
import sys
import subprocess
import time
from pathlib import Path

# Get project root directory
PROJECT_ROOT = Path(__file__).parent.parent

def run_test(test_path: str, description: str) -> bool:
    """Run a test script and return success status"""
    print(f"\n{'=' * 60}")
    print(f"Running: {description}")
    print(f"Script: {test_path}")
    print('=' * 60)

    start_time = time.time()

    try:
        # Change to project root directory
        os.chdir(PROJECT_ROOT)

        # Run the test
        result = subprocess.run([
            sys.executable, test_path
        ], capture_output=False, text=True)

        elapsed = time.time() - start_time

        if result.returncode == 0:
            print(f"\n✅ {description} PASSED ({elapsed:.1f}s)")
            return True
        else:
            print(f"\n❌ {description} FAILED ({elapsed:.1f}s)")
            return False

    except Exception as e:
        elapsed = time.time() - start_time
        print(f"\n❌ {description} ERROR: {e} ({elapsed:.1f}s)")
        return False

def main():
    """Run complete validation suite"""
    import sys

    print("IRIS Graph-AI Complete Validation Suite")
    print("=" * 60)
    print("This will validate all documented capabilities and ensure")
    print("that the documentation accurately reflects system reality.")
    print()
    print("Make sure you have run: ./scripts/setup/setup-test-env.sh")
    print()

    # Check for auto-run flag
    auto_run = '--auto' in sys.argv or '--yes' in sys.argv

    if not auto_run:
        # Ask for confirmation
        response = input("Continue with validation? (y/N): ")
        if response.lower() != 'y':
            print("Validation cancelled.")
            return

    # Track test results
    test_results = []

    # Test 1: Schema Validation
    test_results.append(run_test(
        "tests/python/test_schema_validation.py",
        "Schema and Documentation Validation"
    ))

    # Test 2: Vector Functions
    test_results.append(run_test(
        "tests/python/test_vector_functions.py",
        "Vector Functions Validation"
    ))

    # Test 3: SQL Patterns
    test_results.append(run_test(
        "tests/python/test_sql_queries.py",
        "SQL Query Patterns Validation"
    ))

    # Test 4: End-to-End Workflow
    test_results.append(run_test(
        "scripts/demo/end_to_end_workflow.py",
        "End-to-End Workflow Demonstration"
    ))

    # Report final results
    print("\n" + "=" * 60)
    print("FINAL VALIDATION RESULTS")
    print("=" * 60)

    passed = sum(test_results)
    total = len(test_results)

    test_names = [
        "Schema and Documentation Validation",
        "Vector Functions Validation",
        "SQL Query Patterns Validation",
        "End-to-End Workflow Demonstration"
    ]

    for i, (name, result) in enumerate(zip(test_names, test_results)):
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{i+1}. {name}: {status}")

    print()
    print(f"Summary: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 ALL VALIDATIONS PASSED!")
        print("✅ IRIS Graph-AI system is fully operational")
        print("✅ All documentation is accurate and tested")
        print("✅ Ready for biomedical research workloads")
        return True
    else:
        print(f"\n❌ {total - passed} validation(s) failed")
        print("Please review the errors above and fix before proceeding")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)