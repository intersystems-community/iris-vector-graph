#!/usr/bin/env python3
"""
IRIS Fraud Detection Demo (SQL Edition)

Illustrates the "Drop Down to SQL" pattern for users who need:
1. Familiar SQL syntax for reporting
2. Direct integration with existing BI tools
3. Complex multi-table JOINs beyond standard Cypher

Usage:
    python examples/demo_fraud_detection_sql.py
"""

import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from examples.demo_utils import DemoRunner, display_results_table


def main():
    """Run the SQL-based fraud detection demo."""
    runner = DemoRunner("IRIS Fraud Detection Demo (SQL)", total_steps=6)

    try:
        runner.start()

        # Step 1: Connect
        with runner.step("Connecting to InterSystems IRIS"):
            conn = runner.get_connection()
            cursor = conn.cursor()
            print("      Connected to high-performance SQL engine")

        # Step 2: Relational Data Exploration
        with runner.step("Relational entity exploration"):
            cursor.execute(
                """
                SELECT label, COUNT(*) as cnt 
                FROM Graph_KG.rdf_labels 
                GROUP BY label
                """
            )
            rows = cursor.fetchall()
            print("      Current Entity Distribution (SQL):")
            for label, cnt in rows:
                print(f"        - {label}: {cnt}")

        # Step 3: Complex Pattern Detection (SQL Edition)
        with runner.step("Ring pattern detection (SQL JOINs)"):
            # Illustrating a self-join to find money moving in circles
            sql_ring = """
                SELECT DISTINCT e1.o_id as account_id
                FROM Graph_KG.rdf_edges e1
                JOIN Graph_KG.rdf_edges e2 ON e1.o_id = e2.s
                JOIN Graph_KG.rdf_edges e3 ON e2.o_id = e3.s
                WHERE e3.o_id = e1.s
                AND e1.s LIKE 'ACCOUNT:%'
                LIMIT 5
            """
            cursor.execute(sql_ring)
            rings = cursor.fetchall()
            if rings:
                print(f"      Found {len(rings)} accounts in 3-hop cycles:")
                for row in rings:
                    print(f"        - {row[0]}")
            else:
                print("      No 3-hop cycles found in current sample")

        # Step 4: Hybrid Graph + Property Joins
        with runner.step("Property-augmented network analysis"):
            # Combining graph edges with node properties in one SQL statement
            sql_hybrid = """
                SELECT e.o_id as mule_account, COUNT(*) as txn_count, p.val as risk_level
                FROM Graph_KG.rdf_edges e
                JOIN Graph_KG.rdf_props p ON e.o_id = p.s
                WHERE e.p = 'TO_ACCOUNT'
                AND p.key = 'risk_score'
                GROUP BY e.o_id, p.val
                HAVING COUNT(*) > 2
                ORDER BY txn_count DESC
            """
            cursor.execute(sql_hybrid)
            mules = cursor.fetchall()
            if mules:
                print("      High-Risk Mule Accounts (Graph + SQL Props):")
                for acc, cnt, risk in mules[:3]:
                    print(f"        - {acc}: {cnt} txns, Risk Score: {risk}")

        # Step 5: Vector Search "Dropped Down" in SQL
        with runner.step("Vector Anomaly Detection (Native SQL)"):
            if not runner.check_vector_support():
                print("      (VECTOR support requires IRIS 2025.1+)")
            else:
                # Direct SQL Vector functions
                cursor.execute(
                    """
                    SELECT TOP 3 e2.id, VECTOR_COSINE(e1.emb, e2.emb) as sim
                    FROM Graph_KG.kg_NodeEmbeddings e1, Graph_KG.kg_NodeEmbeddings e2
                    WHERE e1.id = 'ACCOUNT:RING_001' AND e2.id != e1.id
                    ORDER BY sim DESC
                    """
                )
                anomalies = cursor.fetchall()
                if anomalies:
                    print("      SQL Vector Alerts (Cosine Similarity):")
                    for acc, sim in anomalies:
                        print(f"        - {acc}: {sim:.4f}")

        # Step 6: Reporting Aggregate
        with runner.step("Reporting: Alert Summary (SQL Grouping)"):
            cursor.execute(
                """
                SELECT p.val as status, COUNT(*) 
                FROM Graph_KG.rdf_props p
                JOIN Graph_KG.rdf_labels l ON p.s = l.s
                WHERE l.label = 'Alert' AND p.key = 'status'
                GROUP BY p.val
                """
            )
            print("      Alert Status Report:")
            for status, cnt in cursor.fetchall():
                print(f"        - {status}: {cnt}")

        runner.finish(success=True)
        return 0

    except Exception as e:
        print(f"\nDemo failed: {e}")
        runner.finish(success=False)
        return 1


if __name__ == "__main__":
    sys.exit(main())
