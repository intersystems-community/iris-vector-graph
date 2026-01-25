#!/usr/bin/env python3
"""
IRIS Biomedical Demo

Interactive demonstration of IRIS Vector Graph biomedical capabilities:
1. Database connectivity
2. Biomedical data availability
3. Vector similarity search
4. Graph traversal (protein interactions)
5. Hybrid search (vector + text)

Usage:
    python examples/demo_biomedical.py
"""

import json
import sys
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from examples.demo_utils import DemoError, DemoRunner, display_results_table, format_count


def main():
    """Run the biomedical demo."""
    runner = DemoRunner("IRIS Biomedical Demo", total_steps=5)

    try:
        runner.start()

        # Step 1: Connect to database
        with runner.step("Connecting to database"):
            conn = runner.get_connection()
            cursor = conn.cursor()

        # Step 2: Check data availability
        with runner.step("Checking data availability"):
            # Count entities
            cursor.execute("SELECT label, COUNT(*) FROM rdf_labels GROUP BY label")
            label_counts = {row[0]: row[1] for row in cursor.fetchall()}

            # Count embeddings
            cursor.execute("SELECT COUNT(*) FROM kg_NodeEmbeddings")
            embedding_count = cursor.fetchone()[0]

            # Count edges
            cursor.execute("SELECT COUNT(*) FROM rdf_edges")
            edge_count = cursor.fetchone()[0]

            biomedical_labels = ["Gene", "Protein", "Disease", "Drug", "Pathway"]
            biomedical_count = sum(label_counts.get(l, 0) for l in biomedical_labels)

            if biomedical_count == 0 and edge_count == 0:
                raise DemoError(
                    "No biomedical data found in database",
                    next_steps=[
                        'Load sample data: python -c "from scripts.setup import load_sample_data; load_sample_data()"',
                        "Or run: python scripts/sample_data_768.sql via IRIS SQL",
                        "Check database connectivity with: python examples/demo_working_system.py",
                    ],
                )

            print(
                f"      Found {biomedical_count} biomedical entities, {edge_count} relationships, {embedding_count} embeddings"
            )

        # Step 3: Vector similarity search
        with runner.step("Vector similarity search"):
            if embedding_count == 0:
                print("      (Skipped - no embeddings available)")
            else:
                # Get a sample entity with embedding
                cursor.execute("SELECT id FROM kg_NodeEmbeddings LIMIT 1")
                sample = cursor.fetchone()

                if sample:
                    sample_id = sample[0]

                    # Check if VECTOR functions available
                    vector_available = runner.check_vector_support()

                    if vector_available:
                        cursor.execute(
                            """
                            SELECT TOP 5 e2.id, VECTOR_COSINE(e1.emb, e2.emb) as similarity
                            FROM kg_NodeEmbeddings e1, kg_NodeEmbeddings e2
                            WHERE e1.id = ?
                            AND e2.id != ?
                            ORDER BY similarity DESC
                        """,
                            (sample_id, sample_id),
                        )

                        similar = cursor.fetchall()
                        print(f"      Found {len(similar)} similar entities to {sample_id}")

                        if similar:
                            for entity_id, score in similar[:3]:
                                print(f"        - {entity_id}: {score:.4f}")
                    else:
                        print("      (VECTOR functions unavailable - requires IRIS 2025.1+)")
                else:
                    print("      (No embeddings to search)")

        # Step 4: Graph traversal
        with runner.step("Graph traversal"):
            if edge_count == 0:
                print("      (Skipped - no relationships available)")
            else:
                # Find an entity with relationships
                cursor.execute(
                    """
                    SELECT s, COUNT(*) as cnt 
                    FROM rdf_edges 
                    GROUP BY s 
                    ORDER BY cnt DESC 
                    LIMIT 1
                """
                )

                result = cursor.fetchone()

                if result:
                    source_id, rel_count = result

                    # Get relationship types
                    cursor.execute(
                        """
                        SELECT p, o_id FROM rdf_edges WHERE s = ? LIMIT 5
                    """,
                        (source_id,),
                    )

                    relationships = cursor.fetchall()

                    print(f"      Entity {source_id} has {rel_count} relationships")
                    for pred, target in relationships[:3]:
                        print(f"        -> {pred} -> {target}")
                else:
                    print("      (No entities with relationships found)")

        # Step 5: Hybrid search
        with runner.step("Hybrid search (vector + text)"):
            # Text search in properties
            cursor.execute(
                """
                SELECT s, val FROM rdf_props 
                WHERE LOWER(val) LIKE '%gene%' OR LOWER(val) LIKE '%protein%'
                LIMIT 5
            """
            )

            text_results = cursor.fetchall()

            print(f"      Text search found {len(text_results)} matches")

            if embedding_count > 0 and runner.check_vector_support():
                print("      Vector search available for hybrid fusion")
            else:
                print("      (Vector component: limited - see vector search step)")

        runner.finish(success=True)

        # Summary
        print()
        print("Validated Capabilities:")
        print("  Database connectivity and schema")
        if embedding_count > 0:
            print("  Vector embeddings loaded")
        if edge_count > 0:
            print("  Graph relationships available")
        if runner.check_vector_support():
            print("  IRIS VECTOR functions operational")
        else:
            print("  IRIS VECTOR functions not available (limited functionality)")

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
