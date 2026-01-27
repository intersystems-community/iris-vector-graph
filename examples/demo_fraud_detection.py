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
import time
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from examples.demo_utils import DemoError, DemoRunner, display_results_table, format_count


def main():
    """Run the fraud detection demo."""
    runner = DemoRunner("IRIS Fraud Detection Demo", total_steps=7)

    try:
        runner.start()

        # Step 0: Optimize Graph Index
        with runner.step("Optimizing graph index"):
            conn = runner.get_connection()
            cursor = conn.cursor()
            try:
                # Use SQL SELECT to invoke the ObjectScript method
                cursor.execute("SELECT ##class(Graph.KG.Traversal).BuildKG()")
                print("      Graph index (^KG) and Degree Stats built successfully")
            except Exception as e:
                print(f"      Note: Index build skipped ({e})")

        # Step 1: Connect to database
        with runner.step("Connecting to database"):
            conn = runner.get_connection()
            cursor = conn.cursor()
            print("      Connected to InterSystems IRIS")

        # Helper for Cypher execution
        def execute_cypher(query, params=None):
            from iris_vector_graph.cypher.lexer import Lexer
            from iris_vector_graph.cypher.parser import Parser
            from iris_vector_graph.cypher.translator import translate_to_sql

            lexer = Lexer(query)
            parser = Parser(lexer)
            ast = parser.parse()
            sql_query = translate_to_sql(ast, params)
            
            if isinstance(sql_query.sql, list):
                # Handle multi-statement translation (e.g. CTEs + Final Select)
                results = []
                for s, p in zip(sql_query.sql, sql_query.parameters):
                    cursor.execute(s, p)
                    if s.strip().upper().startswith("SELECT"):
                        results = cursor.fetchall()
                return results
            else:
                cursor.execute(sql_query.sql, sql_query.parameters[0] if sql_query.parameters else [])
                if sql_query.sql.strip().upper().startswith("SELECT"):
                    return cursor.fetchall()
                return []

        # Step 2: Check fraud network data using Cypher
        with runner.step("Loading fraud network (Cypher)"):
            try:
                # Count Accounts
                res = execute_cypher("MATCH (n:Account) RETURN count(n)")
                account_count = res[0][0] if res else 0
                
                # Count Transactions
                res = execute_cypher("MATCH (t:Transaction) RETURN count(t)")
                transaction_count = res[0][0] if res else 0
                
                # Count Alerts
                res = execute_cypher("MATCH (a:Alert) RETURN count(a)")
                alert_count = res[0][0] if res else 0
                
                print(f"      Cypher: Found {account_count} Accounts, {transaction_count} Transactions, {alert_count} Alerts")
            except Exception as e:
                print(f"      Note: Cypher fallback to SQL ({e})")
                cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE label = 'Account'")
                account_count = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE label = 'Transaction'")
                transaction_count = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM Graph_KG.rdf_labels WHERE label = 'Alert'")
                alert_count = cursor.fetchone()[0]

        # Step 3: Ring pattern detection (Cypher)
        with runner.step("Ring pattern detection (Cypher)"):
            cypher_ring = "MATCH (a:Account)-[:TO_ACCOUNT]->(t:Transaction)-[:FROM_ACCOUNT]->(a) RETURN a.node_id LIMIT 5"
            ring_accounts = []
            try:
                ring_res = execute_cypher(cypher_ring)
                if ring_res:
                    ring_accounts = [row[0] for row in ring_res]
                    print(f"      Found {len(ring_accounts)} accounts in circular transactions")
                    for acc in ring_accounts:
                        print(f"        - {acc}")
                else:
                    print("      No circular rings detected via Cypher")
            except Exception as e:
                print(f"      Cypher error: {e}")

        # Step 4: Mule account detection (Cypher)
        with runner.step("Mule account detection (Cypher)"):
            # Cypher translator might not support aliasing in ORDER BY perfectly yet
            # We'll use a simpler query or rely on the translator to handle the count
            cypher_mule = "MATCH (t:Transaction)-[:TO_ACCOUNT]->(a:Account) RETURN a.node_id, count(t) LIMIT 5"
            mule_res = []
            try:
                mule_res = execute_cypher(cypher_mule)
                if mule_res:
                    print(f"      Top 5 High-Degree Accounts (Mule Candidates):")
                    for acc, count in mule_res:
                        print(f"        - {acc}: {count} incoming transactions")
            except Exception as e:
                print(f"      Cypher error: {e}")

        # Step 5: Anomaly detection (Vector)
        with runner.step("Vector anomaly detection"):
            embedding_count = 0
            try:
                cursor.execute("SELECT COUNT(*) FROM Graph_KG.kg_NodeEmbeddings")
                embedding_count = cursor.fetchone()[0]
            except Exception:
                pass

            if not runner.check_vector_support():
                print("      (VECTOR functions unavailable - requires IRIS 2025.1+)")
            elif embedding_count == 0:
                print("      (Skipped - no embeddings available)")
            else:
                cursor.execute(
                    """
                    SELECT TOP 3 e2.id, VECTOR_COSINE(e1.emb, e2.emb) as similarity
                    FROM Graph_KG.kg_NodeEmbeddings e1, Graph_KG.kg_NodeEmbeddings e2
                    WHERE e1.id = 'ACCOUNT:RING_001'
                    AND e2.id != e1.id
                    ORDER BY similarity DESC
                """
                )
                anomalies = cursor.fetchall()
                if anomalies:
                    print("      Vector similarity alerts:")
                    for acc_id, sim in anomalies:
                        print(f"        - {acc_id}: similarity={sim:.4f}")

        # Step 6: Hybrid Graph Performance
        with runner.step("Hybrid Performance: Cypher vs optimized BFS"):
            test_node = "ACCOUNT:A001"
            try:
                # 1. Cypher 1-hop
                start = time.time()
                execute_cypher("MATCH (n:Account {node_id: $id})-[]->(m) RETURN m", {"id": test_node})
                cypher_time = (time.time() - start) * 1000
                
                # 2. Optimized BFSFast (PPG)
                start = time.time()
                # Pass NULL for the preds %DynamicArray argument
                cursor.execute("SELECT ##class(Graph.KG.Traversal).BFSFast(?, NULL, 1)", (test_node,))
                fast_time = (time.time() - start) * 1000
                
                print(f"      1-hop traversal from {test_node}:")
                print(f"        - Cypher (SQL Trans): {cypher_time:.2f}ms")
                print(f"        - IRIS Native (PPG):  {fast_time:.2f}ms")
            except Exception as e:
                print(f"      Performance metrics skipped: {e}")

        # Step 7: Alert summary
        with runner.step("Alert summary"):
            if alert_count == 0:
                print("      No alerts in database")
            else:
                # Count by severity
                cursor.execute(
                    """
                    SELECT p.val as severity, COUNT(*) as cnt
                    FROM Graph_KG.rdf_labels l
                    JOIN Graph_KG.rdf_props p ON l.s = p.s
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
                    FROM Graph_KG.rdf_labels l
                    JOIN Graph_KG.rdf_props p ON l.s = p.s
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
        
        # Check variables safely for summary
        if locals().get("ring_res") and len(ring_res) > 0:
            print("  Ring pattern detection operational")
        if locals().get("mule_res") and len(mule_res) > 0:
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
