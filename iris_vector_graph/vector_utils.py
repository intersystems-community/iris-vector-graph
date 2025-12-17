#!/usr/bin/env python3
"""
Vector Optimization Utilities

Performance utilities for IRIS vector operations including:
- HNSW index management
- Vector format conversion
- Performance monitoring
- Migration utilities
"""

import json
import time
import logging
from typing import List, Dict, Any, Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)


class VectorOptimizer:
    """
    Vector optimization utilities for IRIS
    """

    def __init__(self, connection):
        """Initialize with IRIS database connection"""
        self.conn = connection

    def check_hnsw_availability(self, table_name: str = "kg_NodeEmbeddings_optimized") -> Dict[str, Any]:
        """
        Check if HNSW-optimized vector search is available

        Args:
            table_name: Name of optimized vector table

        Returns:
            Dictionary with availability status and performance metrics
        """
        cursor = self.conn.cursor()
        try:
            # Check if optimized table exists
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            count = cursor.fetchone()[0]

            if count == 0:
                return {
                    'available': False,
                    'reason': 'Optimized table exists but has no data',
                    'table_name': table_name,
                    'record_count': 0
                }

            # Test performance with a simple query
            test_vector = [0.1] * 768
            start_time = time.time()

            cursor.execute(f"""
                SELECT TOP 5 id, VECTOR_COSINE(emb, TO_VECTOR(?)) as similarity
                FROM {table_name}
                ORDER BY similarity DESC
            """, [json.dumps(test_vector)])

            results = cursor.fetchall()
            query_time = (time.time() - start_time) * 1000  # Convert to milliseconds

            return {
                'available': True,
                'table_name': table_name,
                'record_count': count,
                'query_time_ms': query_time,
                'test_results': len(results),
                'performance_tier': 'excellent' if query_time < 100 else 'good' if query_time < 1000 else 'slow'
            }

        except Exception as e:
            return {
                'available': False,
                'reason': f'HNSW check failed: {str(e)}',
                'table_name': table_name,
                'error': str(e)
            }
        finally:
            cursor.close()

    def migrate_to_optimized(self, source_table: str = "kg_NodeEmbeddings",
                           target_table: str = "kg_NodeEmbeddings_optimized",
                           batch_size: int = 100) -> Dict[str, Any]:
        """
        Migrate vector data from CSV format to optimized VECTOR format

        Args:
            source_table: Source table with CSV embeddings
            target_table: Target table with VECTOR format
            batch_size: Number of records to process per batch

        Returns:
            Migration results
        """
        cursor = self.conn.cursor()
        insert_cursor = self.conn.cursor()

        try:
            # Check source table
            cursor.execute(f"SELECT COUNT(*) FROM {source_table} WHERE emb IS NOT NULL")
            total_count = cursor.fetchone()[0]

            if total_count == 0:
                return {
                    'success': False,
                    'reason': 'No source data to migrate',
                    'migrated': 0,
                    'total': 0
                }

            logger.info(f"Starting migration of {total_count} records from {source_table} to {target_table}")

            # Create target table if it doesn't exist
            create_sql = f"""
                CREATE TABLE IF NOT EXISTS {target_table} (
                    id VARCHAR(256) PRIMARY KEY,
                    emb VECTOR(FLOAT, 768) NOT NULL
                )
            """
            cursor.execute(create_sql)

            # Create HNSW index
            index_sql = f"""
                CREATE INDEX IF NOT EXISTS HNSW_{target_table.replace('.', '_')}_Optimized
                ON {target_table}(emb)
                AS HNSW(M=16, efConstruction=200, Distance='COSINE')
            """
            cursor.execute(index_sql)

            # Migrate in batches
            cursor.execute(f"""
                SELECT id, emb FROM {source_table}
                WHERE emb IS NOT NULL
                GROUP BY id
                HAVING COUNT(*) >= 1
                ORDER BY id
            """)

            migrated = 0
            failed = 0
            start_time = time.time()

            while True:
                batch = cursor.fetchmany(batch_size)
                if not batch:
                    break

                for entity_id, emb_csv in batch:
                    try:
                        # Parse CSV to array
                        if isinstance(emb_csv, str):
                            emb_array = np.fromstring(emb_csv, dtype=float, sep=',')
                        else:
                            emb_array = np.array(emb_csv)

                        # Validate dimension
                        if len(emb_array) != 768:
                            logger.warning(f"Skipping {entity_id}: wrong dimension {len(emb_array)}")
                            failed += 1
                            continue

                        # Insert using TO_VECTOR
                        insert_sql = f"""
                            INSERT INTO {target_table} (id, emb)
                            VALUES (?, TO_VECTOR(?))
                        """
                        insert_cursor.execute(insert_sql, [entity_id, json.dumps(emb_array.tolist())])
                        migrated += 1

                    except Exception as e:
                        logger.warning(f"Failed to migrate {entity_id}: {e}")
                        failed += 1

                # Progress update
                if migrated % (batch_size * 10) == 0:
                    elapsed = time.time() - start_time
                    rate = migrated / elapsed if elapsed > 0 else 0
                    logger.info(f"Migrated {migrated}/{total_count} records ({rate:.1f}/sec)")

            elapsed = time.time() - start_time
            rate = migrated / elapsed if elapsed > 0 else 0

            return {
                'success': True,
                'migrated': migrated,
                'failed': failed,
                'total': total_count,
                'elapsed_seconds': elapsed,
                'records_per_second': rate,
                'source_table': source_table,
                'target_table': target_table
            }

        except Exception as e:
            logger.error(f"Migration failed: {e}")
            return {
                'success': False,
                'reason': f'Migration error: {str(e)}',
                'error': str(e)
            }
        finally:
            cursor.close()
            insert_cursor.close()

    def benchmark_vector_search(self, test_vectors: Optional[List[List[float]]] = None,
                               k: int = 10, iterations: int = 5) -> Dict[str, Any]:
        """
        Benchmark vector search performance

        Args:
            test_vectors: Optional list of test vectors (defaults to random)
            k: Number of results per query
            iterations: Number of test iterations

        Returns:
            Performance benchmark results
        """
        if test_vectors is None:
            # Generate random test vectors
            test_vectors = [np.random.rand(768).tolist() for _ in range(iterations)]

        results = {
            'hnsw_optimized': [],
            'csv_fallback': [],
            'test_iterations': iterations,
            'k': k
        }

        # Test HNSW optimized performance
        try:
            cursor = self.conn.cursor()
            for i, test_vector in enumerate(test_vectors):
                start_time = time.time()

                cursor.execute(f"""
                    SELECT TOP {k} id, VECTOR_COSINE(emb, TO_VECTOR(?)) as similarity
                    FROM kg_NodeEmbeddings_optimized
                    ORDER BY similarity DESC
                """, [json.dumps(test_vector)])

                results_count = len(cursor.fetchall())
                query_time = (time.time() - start_time) * 1000

                results['hnsw_optimized'].append({
                    'iteration': i + 1,
                    'query_time_ms': query_time,
                    'results_count': results_count
                })

            cursor.close()

        except Exception as e:
            results['hnsw_error'] = str(e)

        # Test CSV fallback performance
        try:
            cursor = self.conn.cursor()
            for i, test_vector in enumerate(test_vectors):
                start_time = time.time()

                # Simulate CSV parsing performance
                cursor.execute("SELECT TOP 100 id, emb FROM kg_NodeEmbeddings WHERE emb IS NOT NULL")
                rows = cursor.fetchall()

                query_vector = np.array(test_vector)
                similarities = []

                for entity_id, emb_csv in rows:
                    try:
                        emb_array = np.fromstring(emb_csv, dtype=float, sep=',')
                        cos_sim = np.dot(query_vector, emb_array) / (np.linalg.norm(query_vector) * np.linalg.norm(emb_array))
                        similarities.append((entity_id, cos_sim))
                    except:
                        continue

                similarities.sort(key=lambda x: x[1], reverse=True)
                query_time = (time.time() - start_time) * 1000

                results['csv_fallback'].append({
                    'iteration': i + 1,
                    'query_time_ms': query_time,
                    'results_count': min(len(similarities), k)
                })

            cursor.close()

        except Exception as e:
            results['csv_error'] = str(e)

        # Calculate summary statistics
        if results['hnsw_optimized']:
            hnsw_times = [r['query_time_ms'] for r in results['hnsw_optimized']]
            results['hnsw_summary'] = {
                'avg_time_ms': np.mean(hnsw_times),
                'min_time_ms': np.min(hnsw_times),
                'max_time_ms': np.max(hnsw_times),
                'std_time_ms': np.std(hnsw_times)
            }

        if results['csv_fallback']:
            csv_times = [r['query_time_ms'] for r in results['csv_fallback']]
            results['csv_summary'] = {
                'avg_time_ms': np.mean(csv_times),
                'min_time_ms': np.min(csv_times),
                'max_time_ms': np.max(csv_times),
                'std_time_ms': np.std(csv_times)
            }

        # Calculate performance improvement
        if 'hnsw_summary' in results and 'csv_summary' in results:
            improvement = results['csv_summary']['avg_time_ms'] / results['hnsw_summary']['avg_time_ms']
            results['performance_improvement'] = {
                'speedup_factor': improvement,
                'hnsw_avg_ms': results['hnsw_summary']['avg_time_ms'],
                'csv_avg_ms': results['csv_summary']['avg_time_ms']
            }

        return results

    def optimize_hnsw_parameters(self, m_values: List[int] = [8, 16, 32],
                                ef_values: List[int] = [100, 200, 400]) -> Dict[str, Any]:
        """
        Optimize HNSW index parameters for best performance

        Args:
            m_values: List of M parameter values to test
            ef_values: List of efConstruction values to test

        Returns:
            Optimization results with best parameters
        """
        # This would require recreating indexes with different parameters
        # For now, return recommended defaults based on research
        return {
            'recommended_m': 16,
            'recommended_ef_construction': 200,
            'reasoning': 'M=16 provides good recall/performance balance, efConstruction=200 improves accuracy',
            'notes': 'Actual optimization requires index recreation which is expensive'
        }

    def get_vector_statistics(self, table_name: str = "kg_NodeEmbeddings_optimized") -> Dict[str, Any]:
        """
        Get statistics about vector data

        Args:
            table_name: Vector table to analyze

        Returns:
            Vector statistics
        """
        cursor = self.conn.cursor()
        try:
            # Basic counts
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            total_count = cursor.fetchone()[0]

            if total_count == 0:
                return {'error': 'No vector data found'}

            # Sample vector for dimension analysis
            cursor.execute(f"SELECT TOP 1 emb FROM {table_name}")
            sample_vector = cursor.fetchone()[0]

            stats = {
                'total_vectors': total_count,
                'vector_dimension': len(sample_vector) if hasattr(sample_vector, '__len__') else 'unknown',
                'table_name': table_name,
                'sample_available': True
            }

            return stats

        except Exception as e:
            return {
                'error': str(e),
                'table_name': table_name
            }
        finally:
            cursor.close()