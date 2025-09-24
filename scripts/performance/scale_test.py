#!/usr/bin/env python3
"""
Large-scale IRIS graph ingestion and query performance test
Generates realistic biomedical knowledge graph data at scale
"""

import json
import time
import random
import numpy as np
import pyodbc
from concurrent.futures import ThreadPoolExecutor
import logging
from dataclasses import dataclass
from typing import List, Dict, Tuple
import argparse

# Test configuration
@dataclass
class ScaleTestConfig:
    # Data scale
    num_entities: int = 100_000      # Nodes in the graph
    num_relationships: int = 500_000  # Edges in the graph
    num_documents: int = 50_000      # Text documents
    avg_edges_per_node: int = 5      # Graph connectivity

    # Vector configuration
    vector_dimension: int = 768      # OpenAI ada-002 compatible

    # Performance test parameters
    num_vector_queries: int = 1000   # Vector search tests
    num_text_queries: int = 1000     # Text search tests
    num_hybrid_queries: int = 500    # Hybrid search tests
    num_graph_queries: int = 500     # Graph traversal tests

    # Batch sizes for ingestion
    entity_batch_size: int = 10_000
    edge_batch_size: int = 50_000
    embedding_batch_size: int = 5_000
    document_batch_size: int = 5_000

    # Concurrency
    max_workers: int = 8

# Realistic biomedical entity types and relationships
ENTITY_TYPES = [
    "Gene", "Protein", "Disease", "Drug", "Pathway", "Tissue",
    "CellType", "Phenotype", "Chemical", "Organism"
]

RELATIONSHIP_TYPES = [
    "interacts_with", "causes", "treats", "expresses", "regulates",
    "participates_in", "located_in", "associated_with", "inhibits",
    "activates", "binds_to", "metabolizes"
]

# Sample biomedical terms for realistic text generation
BIOMEDICAL_TERMS = [
    "cancer", "tumor", "metastasis", "oncology", "therapy", "treatment",
    "protein", "gene", "expression", "mutation", "pathway", "signaling",
    "cell", "tissue", "organ", "development", "disease", "syndrome",
    "drug", "compound", "inhibitor", "receptor", "enzyme", "metabolism",
    "clinical", "patient", "trial", "efficacy", "toxicity", "biomarker"
]

class ScaleTestSuite:
    def __init__(self, config: ScaleTestConfig):
        self.config = config
        self.setup_logging()
        self.setup_database()

    def setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('scale_test.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    def setup_database(self):
        """Connect to IRIS database"""
        try:
            self.conn = pyodbc.connect(
                'DSN=IRIS_DEV;UID=_SYSTEM;PWD=SYS'
            )
            self.logger.info("Connected to IRIS database")
        except Exception as e:
            self.logger.error(f"Database connection failed: {e}")
            raise

    def generate_realistic_vector(self) -> List[float]:
        """Generate realistic 768D embedding vector"""
        # Use normal distribution with some structure
        vector = np.random.normal(0, 0.1, self.config.vector_dimension)
        # Add some clustering structure
        cluster_size = 64
        for i in range(0, self.config.vector_dimension, cluster_size):
            cluster_bias = np.random.normal(0, 0.2)
            vector[i:i+cluster_size] += cluster_bias
        # Normalize
        vector = vector / np.linalg.norm(vector)
        return vector.tolist()

    def generate_realistic_text(self, length: int = 200) -> str:
        """Generate realistic biomedical text"""
        words = []
        for _ in range(length // 10):
            # Mix biomedical terms with common words
            if random.random() < 0.3:
                words.append(random.choice(BIOMEDICAL_TERMS))
            else:
                words.append(random.choice([
                    "the", "and", "of", "in", "to", "is", "that", "for",
                    "with", "as", "by", "this", "from", "on", "at", "be",
                    "study", "analysis", "results", "data", "method", "approach"
                ]))
        return " ".join(words)

    def bulk_insert_entities(self, batch_start: int, batch_size: int):
        """Insert a batch of entities"""
        cursor = self.conn.cursor()
        start_time = time.time()

        try:
            # Insert into rdf_labels and rdf_props
            for i in range(batch_start, min(batch_start + batch_size, self.config.num_entities)):
                entity_id = f"entity:{i}"
                entity_type = random.choice(ENTITY_TYPES)
                entity_name = f"{entity_type}_{i}"

                # Insert label
                cursor.execute(
                    "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
                    entity_id, entity_type
                )

                # Insert name property
                cursor.execute(
                    "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                    entity_id, "name", entity_name
                )

                # Add some additional properties
                if random.random() < 0.3:
                    cursor.execute(
                        "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                        entity_id, "description", self.generate_realistic_text(50)
                    )

            self.conn.commit()
            duration = time.time() - start_time
            self.logger.info(f"Inserted entities batch {batch_start}-{batch_start+batch_size} in {duration:.2f}s")

        except Exception as e:
            self.logger.error(f"Entity insertion failed: {e}")
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def bulk_insert_edges(self, batch_start: int, batch_size: int):
        """Insert a batch of edges"""
        cursor = self.conn.cursor()
        start_time = time.time()

        try:
            for i in range(batch_start, min(batch_start + batch_size, self.config.num_relationships)):
                # Random source and target entities
                source_id = f"entity:{random.randint(0, self.config.num_entities-1)}"
                target_id = f"entity:{random.randint(0, self.config.num_entities-1)}"

                # Avoid self-loops
                if source_id != target_id:
                    relationship = random.choice(RELATIONSHIP_TYPES)
                    cursor.execute(
                        "INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                        source_id, relationship, target_id
                    )

            self.conn.commit()
            duration = time.time() - start_time
            self.logger.info(f"Inserted edges batch {batch_start}-{batch_start+batch_size} in {duration:.2f}s")

        except Exception as e:
            self.logger.error(f"Edge insertion failed: {e}")
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def bulk_insert_embeddings(self, batch_start: int, batch_size: int):
        """Insert a batch of embeddings"""
        cursor = self.conn.cursor()
        start_time = time.time()

        try:
            for i in range(batch_start, min(batch_start + batch_size, self.config.num_entities)):
                entity_id = f"entity:{i}"
                vector = self.generate_realistic_vector()
                vector_json = json.dumps(vector)

                cursor.execute(
                    "INSERT INTO kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?))",
                    entity_id, vector_json
                )

            self.conn.commit()
            duration = time.time() - start_time
            rate = batch_size / duration if duration > 0 else 0
            self.logger.info(f"Inserted embeddings batch {batch_start}-{batch_start+batch_size} in {duration:.2f}s ({rate:.0f} vectors/sec)")

        except Exception as e:
            self.logger.error(f"Embedding insertion failed: {e}")
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def bulk_insert_documents(self, batch_start: int, batch_size: int):
        """Insert a batch of documents"""
        cursor = self.conn.cursor()
        start_time = time.time()

        try:
            for i in range(batch_start, min(batch_start + batch_size, self.config.num_documents)):
                # Link to random entity
                node_id = random.randint(0, self.config.num_entities-1)
                text = self.generate_realistic_text(random.randint(100, 500))

                cursor.execute(
                    "INSERT INTO kg_Documents (node_id, txt) VALUES (?, ?)",
                    node_id, text
                )

            self.conn.commit()
            duration = time.time() - start_time
            self.logger.info(f"Inserted documents batch {batch_start}-{batch_start+batch_size} in {duration:.2f}s")

        except Exception as e:
            self.logger.error(f"Document insertion failed: {e}")
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def ingest_data_parallel(self):
        """Parallel data ingestion"""
        self.logger.info("Starting large-scale data ingestion...")

        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            # Submit entity insertion jobs
            entity_futures = []
            for start in range(0, self.config.num_entities, self.config.entity_batch_size):
                future = executor.submit(self.bulk_insert_entities, start, self.config.entity_batch_size)
                entity_futures.append(future)

            # Wait for entities to complete before edges
            for future in entity_futures:
                future.result()

            self.logger.info("Entity insertion completed. Starting edges...")

            # Submit edge insertion jobs
            edge_futures = []
            for start in range(0, self.config.num_relationships, self.config.edge_batch_size):
                future = executor.submit(self.bulk_insert_edges, start, self.config.edge_batch_size)
                edge_futures.append(future)

            # Submit embedding insertion jobs in parallel with edges
            embedding_futures = []
            for start in range(0, self.config.num_entities, self.config.embedding_batch_size):
                future = executor.submit(self.bulk_insert_embeddings, start, self.config.embedding_batch_size)
                embedding_futures.append(future)

            # Submit document insertion jobs
            document_futures = []
            for start in range(0, self.config.num_documents, self.config.document_batch_size):
                future = executor.submit(self.bulk_insert_documents, start, self.config.document_batch_size)
                document_futures.append(future)

            # Wait for all to complete
            for future in edge_futures + embedding_futures + document_futures:
                future.result()

        self.logger.info("Data ingestion completed!")

    def benchmark_vector_search(self) -> Dict:
        """Benchmark vector similarity search"""
        self.logger.info("Benchmarking vector search...")
        cursor = self.conn.cursor()

        times = []
        results_counts = []

        for i in range(self.config.num_vector_queries):
            # Generate random query vector
            query_vector = json.dumps(self.generate_realistic_vector())
            k = random.choice([10, 50, 100])

            start_time = time.time()
            cursor.execute(
                "SELECT * FROM TABLE(kg_KNN_VEC_JSON(?, ?, NULL))",
                query_vector, k
            )
            results = cursor.fetchall()
            duration = time.time() - start_time

            times.append(duration)
            results_counts.append(len(results))

            if i % 100 == 0:
                self.logger.info(f"Vector search progress: {i}/{self.config.num_vector_queries}")

        cursor.close()

        return {
            "avg_time": np.mean(times),
            "p95_time": np.percentile(times, 95),
            "p99_time": np.percentile(times, 99),
            "avg_results": np.mean(results_counts),
            "total_queries": len(times)
        }

    def benchmark_text_search(self) -> Dict:
        """Benchmark text search"""
        self.logger.info("Benchmarking text search...")
        cursor = self.conn.cursor()

        times = []
        results_counts = []

        for i in range(self.config.num_text_queries):
            # Generate random query
            query_terms = random.sample(BIOMEDICAL_TERMS, random.randint(1, 3))
            query = " ".join(query_terms)

            start_time = time.time()
            cursor.execute(
                "SELECT * FROM TABLE(kg_TXT(?, '1', 'en', 50))",
                query
            )
            results = cursor.fetchall()
            duration = time.time() - start_time

            times.append(duration)
            results_counts.append(len(results))

            if i % 100 == 0:
                self.logger.info(f"Text search progress: {i}/{self.config.num_text_queries}")

        cursor.close()

        return {
            "avg_time": np.mean(times),
            "p95_time": np.percentile(times, 95),
            "p99_time": np.percentile(times, 99),
            "avg_results": np.mean(results_counts),
            "total_queries": len(times)
        }

    def run_scale_test(self):
        """Execute complete scale test"""
        start_time = time.time()

        # Data ingestion phase
        self.logger.info("=== PHASE 1: DATA INGESTION ===")
        ingestion_start = time.time()
        self.ingest_data_parallel()
        ingestion_time = time.time() - ingestion_start

        # Build indexes
        self.logger.info("=== BUILDING INDEXES ===")
        cursor = self.conn.cursor()
        index_start = time.time()
        cursor.execute("BUILD INDEX ON TABLE kg_NodeEmbeddings")
        cursor.execute("BUILD INDEX ON TABLE kg_Documents")
        cursor.execute("TUNE TABLE kg_NodeEmbeddings")
        cursor.execute("TUNE TABLE kg_Documents")
        cursor.close()
        index_time = time.time() - index_start

        # Query benchmarks
        self.logger.info("=== PHASE 2: QUERY BENCHMARKS ===")
        vector_results = self.benchmark_vector_search()
        text_results = self.benchmark_text_search()

        total_time = time.time() - start_time

        # Generate report
        report = {
            "config": self.config.__dict__,
            "timings": {
                "total_time": total_time,
                "ingestion_time": ingestion_time,
                "index_build_time": index_time
            },
            "ingestion_rates": {
                "entities_per_sec": self.config.num_entities / ingestion_time,
                "edges_per_sec": self.config.num_relationships / ingestion_time,
                "embeddings_per_sec": self.config.num_entities / ingestion_time,
                "documents_per_sec": self.config.num_documents / ingestion_time
            },
            "vector_search_performance": vector_results,
            "text_search_performance": text_results
        }

        # Save report
        with open('scale_test_report.json', 'w') as f:
            json.dump(report, f, indent=2)

        self.logger.info("=== SCALE TEST COMPLETE ===")
        self.logger.info(f"Total time: {total_time:.2f} seconds")
        self.logger.info(f"Ingestion rate: {self.config.num_entities/ingestion_time:.0f} entities/sec")
        self.logger.info(f"Vector search avg: {vector_results['avg_time']*1000:.2f}ms")
        self.logger.info(f"Text search avg: {text_results['avg_time']*1000:.2f}ms")

        return report

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IRIS Graph Scale Test")
    parser.add_argument("--entities", type=int, default=100_000, help="Number of entities")
    parser.add_argument("--edges", type=int, default=500_000, help="Number of edges")
    parser.add_argument("--documents", type=int, default=50_000, help="Number of documents")
    parser.add_argument("--workers", type=int, default=8, help="Max worker threads")

    args = parser.parse_args()

    config = ScaleTestConfig(
        num_entities=args.entities,
        num_relationships=args.edges,
        num_documents=args.documents,
        max_workers=args.workers
    )

    test_suite = ScaleTestSuite(config)
    test_suite.run_scale_test()