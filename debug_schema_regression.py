#!/usr/bin/env python3
"""
Debug script to investigate schema initialization regression.

Build 1: 11/16 tables created (with INVALID rdf_edges SQL)
Builds 2-5: 5/16 tables created (with various rdf_edges attempts)

This script tests the execute_schema_sql() method to understand why
builds 2-5 create fewer tables than build 1.
"""

import sys
from iris_vector_graph.schema import GraphSchema


class MockCursor:
    """Mock cursor to capture SQL execution without hitting real IRIS"""

    def __init__(self, simulate_error_on_rdf_edges=None):
        self.executed_statements = []
        self.error_mode = simulate_error_on_rdf_edges

    def execute(self, sql):
        """Capture SQL and simulate errors"""
        self.executed_statements.append(sql)

        # Simulate rdf_edges errors based on mode
        if self.error_mode and 'rdf_edges' in sql and 'CREATE TABLE' in sql:
            if self.error_mode == "invalid_sql":
                # Build 1 error: "Invalid SQL statement - ) expected, IDENTIFIER (GEN)"
                raise Exception("Invalid SQL statement - ) expected, IDENTIFIER (GEN)")
            elif self.error_mode == "error_5373":
                # Builds 2-5 error: "ERROR #5373: Class 'User..."
                raise Exception("ERROR #5373: Class 'User.rdf_edges' does not exist")


def test_schema_execution(error_mode):
    """Test schema execution with different error modes"""
    print(f"\n{'='*70}")
    print(f"Testing with error_mode: {error_mode}")
    print(f"{'='*70}\n")

    # Get schema SQL
    schema_sql = GraphSchema.get_base_schema_sql()

    # Count total CREATE TABLE statements in schema
    create_table_count = schema_sql.count('CREATE TABLE')
    print(f"Total CREATE TABLE statements in schema: {create_table_count}")

    # Create mock cursor
    mock_cursor = MockCursor(simulate_error_on_rdf_edges=error_mode)

    # Execute schema
    results = GraphSchema.execute_schema_sql(mock_cursor, schema_sql)

    # Analyze results
    print(f"\nTotal statements processed: {len(results)}")
    print(f"Statements executed: {len(mock_cursor.executed_statements)}")

    # Count CREATE TABLE statements that were attempted
    create_table_attempts = sum(
        1 for stmt in mock_cursor.executed_statements
        if 'CREATE TABLE' in stmt.upper()
    )
    print(f"CREATE TABLE attempts: {create_table_attempts}")

    # Show first few results
    print(f"\nFirst 10 results:")
    for i, (stmt_preview, status) in enumerate(list(results.items())[:10]):
        print(f"  {i+1}. {stmt_preview}: {status}")

    # Count successes vs errors
    successes = sum(1 for status in results.values() if 'success' in status)
    skipped = sum(1 for status in results.values() if 'skipped' in status)
    errors = sum(1 for status in results.values() if 'error' in status)

    print(f"\nResult summary:")
    print(f"  Successes: {successes}")
    print(f"  Skipped: {skipped}")
    print(f"  Errors: {errors}")

    # Find the rdf_edges error
    for stmt_preview, status in results.items():
        if 'error' in status and 'rdf_edges' in stmt_preview:
            print(f"\nrdf_edges error:")
            print(f"  Statement: {stmt_preview}")
            print(f"  Status: {status}")
            break

    return {
        'total_statements': len(results),
        'statements_executed': len(mock_cursor.executed_statements),
        'create_table_attempts': create_table_attempts,
        'successes': successes,
        'skipped': skipped,
        'errors': errors
    }


if __name__ == '__main__':
    # Test both error modes
    print("\n" + "="*70)
    print("SCHEMA EXECUTION REGRESSION DEBUG")
    print("="*70)

    # Test 1: No errors (baseline)
    results_baseline = test_schema_execution(None)

    # Test 2: Build 1 error (Invalid SQL)
    results_build1 = test_schema_execution("invalid_sql")

    # Test 3: Builds 2-5 error (ERROR #5373)
    results_builds25 = test_schema_execution("error_5373")

    # Compare results
    print(f"\n{'='*70}")
    print("COMPARISON")
    print(f"{'='*70}\n")

    print(f"{'Metric':<30} {'Baseline':<15} {'Build 1':<15} {'Builds 2-5':<15}")
    print(f"{'-'*75}")

    for metric in ['total_statements', 'statements_executed', 'create_table_attempts', 'successes', 'errors']:
        print(f"{metric:<30} {results_baseline[metric]:<15} {results_build1[metric]:<15} {results_builds25[metric]:<15}")

    print(f"\n{'='*70}")
    print("KEY FINDING:")
    print(f"{'='*70}")

    if results_build1['create_table_attempts'] != results_builds25['create_table_attempts']:
        print(f"❌ REGRESSION CONFIRMED: Build 1 attempted {results_build1['create_table_attempts']} tables, "
              f"Builds 2-5 attempted {results_builds25['create_table_attempts']} tables")
    else:
        print(f"✅ NO REGRESSION: Both build 1 and builds 2-5 attempted {results_build1['create_table_attempts']} tables")
        print(f"   The difference in table counts (11/16 vs 5/16) must be from iris-vector-rag, not iris-vector-graph")
