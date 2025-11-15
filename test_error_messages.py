#!/usr/bin/env python3
"""
Test how different error messages are categorized by execute_schema_sql()
"""

# Test different error messages against the error handling logic
test_errors = [
    ("Invalid SQL statement - ) expected, IDENTIFIER (GEN)", "Build 1"),
    ("ERROR #5373: Class 'User.rdf_edges' does not exist", "Builds 2-5"),
    ("Table 'rdf_edges' already exists", "Already exists case"),
    ("Duplicate key", "Duplicate case"),
]

for error_msg, label in test_errors:
    error_msg_lower = error_msg.lower()

    # Current error handling logic (from schema.py lines 57-61)
    if 'already exists' in error_msg_lower or 'duplicate' in error_msg_lower or 'table exists' in error_msg_lower:
        result = "SKIPPED (already exists)"
    else:
        result = f"ERROR: {error_msg[:100]}"

    print(f"{label:<20}: {result}")
