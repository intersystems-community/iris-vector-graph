#!/usr/bin/env python3
"""
IRIS Fraud Detection Demo

Interactive demonstration of IRIS Vector Graph fraud detection capabilities:
1. Database connectivity
2. Fraud network data availability
3. Ring pattern detection (money laundering)
4. Mule account detection (high-degree nodes)
5. Anomaly detection (vector similarity)
6. Alert summary

Usage:
    python examples/demo_fraud_detection.py
"""

import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from examples.demo_utils import DemoError, DemoRunner, display_results_table, format_count


def main():
    """Run the fraud detection demo."""
    runner = DemoRunner("IRIS Fraud Detection Demo", total_steps=6)

    try:
        runner.start()

        # Step 1: Connect to database
        with runner.step("Connecting to database"):
            conn = runner.get_connection()
            cursor = conn.cursor()

        # Step 2: Check fraud network data
        with runner.step("Loading fraud network"):
            # Count fraud entities (case-insensitive for compatibility)
            cursor.execute(
                """
                SELECT UPPER(label), COUNT(*) 
                FROM rdf_labels 
                WHERE UPPER(label) IN ('ACCOUNT', 'TRANSACTION', 'ALERT')
                GROUP BY UPPER(label)
            """
            )

            counts = {row[0]: row[1] for row in cursor.fetchall()}

            account_count = counts.get("ACCOUNT", 0)
            transaction_count = counts.get("TRANSACTION", 0)
            alert_count = counts.get("ALERT", 0)

            if account_count == 0:
                raise DemoError(
                    "No fraud data found in database",
                    next_steps=[
                        'Load fraud sample data: python -c "from scripts.setup import load_fraud_data; load_fraud_data()"',
                        "Or manually run: sql/fraud_sample_data.sql via IRIS SQL",
                        "Check database connectivity with: python examples/demo_working_system.py",
                    ],
                )

            print(
                f"      Found {account_count} accounts, {transaction_count} transactions, {alert_count} alerts"
            )

        # Step 3: Ring pattern detection
        with runner.step("Ring pattern detection"):
            # Find accounts that participate in both incoming and outgoing transactions
            cursor.execute(
                """
                SELECT DISTINCT e1.o_id as account_id
                FROM rdf_edges e1
                WHERE e1.p IN ('FROM_ACCOUNT', 'TO_ACCOUNT')
                AND EXISTS (
                    SELECT 1 FROM rdf_edges e2 
                    WHERE e2.o_id = e1.o_id 
                    AND e2.p != e1.p
                )
                AND e1.o_id LIKE 'ACCOUNT:RING%'
            """
            )

            ring_accounts = [row[0] for row in cursor.fetchall()]

            if ring_accounts:
                print(f"      Found {len(ring_accounts)} accounts in ring patterns")
                for acc in ring_accounts[:3]:
                    print(f"        - {acc}")
                if len(ring_accounts) > 3:
                    print(f"        ... and {len(ring_accounts) - 3} more")
            else:
                # Try broader detection
                cursor.execute(
                    """
                    SELECT o_id, COUNT(*) as edge_count
                    FROM rdf_edges
                    WHERE p IN ('FROM_ACCOUNT', 'TO_ACCOUNT')
                    GROUP BY o_id
                    HAVING COUNT(*) >= 2
                    ORDER BY edge_count DESC
                    LIMIT 5
                """
                )

                multi_edge = cursor.fetchall()
                if multi_edge:
                    print(f"      Found {len(multi_edge)} accounts with multiple transaction edges")
                else:
                    print("      No ring patterns detected in current data")

        # Step 4: Mule account detection
        with runner.step("Mule account detection"):
            # Find high-degree nodes (accounts with many counterparties)
            cursor.execute(
                """
                SELECT TOP 5 o_id as account_id, COUNT(DISTINCT s) as txn_count
                FROM rdf_edges
                WHERE p IN ('FROM_ACCOUNT', 'TO_ACCOUNT')
                AND o_id LIKE 'ACCOUNT:MULE%'
                GROUP BY o_id
                ORDER BY txn_count DESC
            """
            )

            mule_accounts = cursor.fetchall()

            if mule_accounts:
                print(f"      Found {len(mule_accounts)} potential mule accounts")
                for acc, count in mule_accounts[:2]:
                    # Get risk score
                    cursor.execute(
                        "SELECT val FROM rdf_props WHERE s = ? AND key = 'risk_score'", (acc,)
                    )
                    risk = cursor.fetchone()
                    risk_str = f"risk={risk[0]}" if risk else "risk=N/A"
                    print(f"        - {acc}: {count} transactions, {risk_str}")
            else:
                # Broader search
                cursor.execute(
                    """
                    SELECT TOP 3 o_id, COUNT(*) as cnt
                    FROM rdf_edges
                    WHERE p IN ('FROM_ACCOUNT', 'TO_ACCOUNT')
                    GROUP BY o_id
                    ORDER BY cnt DESC
                """
                )

                top_accounts = cursor.fetchall()
                if top_accounts:
                    print(f"      Top connected accounts:")
                    for acc, cnt in top_accounts:
                        print(f"        - {acc}: {cnt} transaction edges")
                else:
                    print("      No mule account patterns detected")

        # Step 5: Anomaly detection
        with runner.step("Anomaly detection (vector)"):
            # Check for account embeddings
            cursor.execute(
                """
                SELECT COUNT(*) FROM kg_NodeEmbeddings 
                WHERE id LIKE 'ACCOUNT:%'
            """
            )

            embedding_count = cursor.fetchone()[0]

            if embedding_count == 0:
                print("      (Skipped - no account embeddings available)")
            elif not runner.check_vector_support():
                print("      (VECTOR functions unavailable - requires IRIS 2025.1+)")
            else:
                # Find most anomalous accounts (lowest similarity to normal)
                cursor.execute(
                    """
                    SELECT id FROM kg_NodeEmbeddings 
                    WHERE id = 'ACCOUNT:A001'
                """
                )

                baseline = cursor.fetchone()

                if baseline:
                    cursor.execute(
                        """
                        SELECT TOP 5 e2.id, VECTOR_COSINE(e1.emb, e2.emb) as similarity
                        FROM kg_NodeEmbeddings e1, kg_NodeEmbeddings e2
                        WHERE e1.id = 'ACCOUNT:A001'
                        AND e2.id != 'ACCOUNT:A001'
                        AND e2.id LIKE 'ACCOUNT:%'
                        ORDER BY similarity ASC
                    """
                    )

                    anomalies = cursor.fetchall()

                    if anomalies:
                        print(
                            f"      Found {len(anomalies)} potential anomalies (lowest similarity to baseline)"
                        )
                        for acc_id, sim in anomalies[:3]:
                            print(f"        - {acc_id}: similarity={sim:.4f}")
                    else:
                        print("      No anomalies detected")
                else:
                    print("      (Baseline account not found for comparison)")

        # Step 6: Alert summary
        with runner.step("Alert summary"):
            if alert_count == 0:
                print("      No alerts in database")
            else:
                # Count by severity
                cursor.execute(
                    """
                    SELECT p.val as severity, COUNT(*) as cnt
                    FROM rdf_labels l
                    JOIN rdf_props p ON l.s = p.s
                    WHERE l.label = 'Alert'
                    AND p.key = 'severity'
                    GROUP BY p.val
                    ORDER BY 
                        CASE p.val 
                            WHEN 'critical' THEN 1 
                            WHEN 'high' THEN 2 
                            WHEN 'medium' THEN 3 
                            WHEN 'low' THEN 4 
                            ELSE 5 
                        END
                """
                )

                severity_counts = cursor.fetchall()

                # Count by status
                cursor.execute(
                    """
                    SELECT p.val as status, COUNT(*) as cnt
                    FROM rdf_labels l
                    JOIN rdf_props p ON l.s = p.s
                    WHERE l.label = 'Alert'
                    AND p.key = 'status'
                    GROUP BY p.val
                """
                )

                status_counts = {row[0]: row[1] for row in cursor.fetchall()}

                print(f"      {alert_count} total alerts")

                if severity_counts:
                    print("      By severity:")
                    for sev, cnt in severity_counts:
                        print(f"        - {sev}: {cnt}")

                open_count = status_counts.get("open", 0)
                if open_count > 0:
                    print(f"      {open_count} alerts require attention (status=open)")

        runner.finish(success=True)

        # Summary
        print()
        print("Fraud Detection Capabilities Validated:")
        print("  Database connectivity and fraud schema")
        print(f"  Fraud network: {account_count} accounts, {transaction_count} transactions")
        if len(ring_accounts) > 0 if "ring_accounts" in dir() else False:
            print("  Ring pattern detection operational")
        if len(mule_accounts) > 0 if "mule_accounts" in dir() else False:
            print("  Mule account detection operational")
        if runner.check_vector_support() and embedding_count > 0:
            print("  Vector anomaly detection operational")
        if alert_count > 0:
            print(f"  Alert system: {alert_count} alerts tracked")

        return 0

    except DemoError as e:
        e.display()
        runner.finish(success=False)
        return 1
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        import traceback

        traceback.print_exc()
        runner.finish(success=False)
        return 1


if __name__ == "__main__":
    sys.exit(main())
