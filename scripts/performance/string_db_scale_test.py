#!/usr/bin/env python3
"""
Large-scale IRIS graph test using STRING protein interaction database
Downloads and processes real biomedical data at massive scale
"""

import json
import time
import random
import numpy as np
import gzip
import urllib.request
import os
import re
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

try:
    import iris
    IRIS_AVAILABLE = True
except ImportError:
    IRIS_AVAILABLE = False
    print("Warning: intersystems_irispython not available, install with: pip install intersystems_irispython")
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from dataclasses import dataclass
from typing import List, Dict, Tuple, Set
import argparse
import hashlib
import csv

@dataclass
class StringScaleTestConfig:
    # STRING database configuration
    string_version: str = "v12.0"
    organism: str = "9606"  # Human (Homo sapiens)

    # Data scale limits (for testing - STRING is HUGE)
    max_proteins: int = 100_000      # Limit proteins for testing
    max_interactions: int = 1_000_000 # Limit interactions for testing
    min_score: int = 400             # Minimum STRING confidence score (0-1000)

    # Vector configuration
    vector_dimension: int = 768      # OpenAI ada-002 compatible

    # Performance test parameters
    num_vector_queries: int = 1000   # Vector search tests
    num_text_queries: int = 1000     # Text search tests
    num_hybrid_queries: int = 500    # Hybrid search tests
    num_graph_queries: int = 500     # Graph traversal tests

    # Batch sizes for ingestion
    protein_batch_size: int = 5_000
    interaction_batch_size: int = 25_000
    embedding_batch_size: int = 2_000

    # Concurrency
    max_workers: int = 12

    # Data directory
    data_dir: str = "/Users/tdyar/ws/graph-ai/data/string"

def validate_vector_string(vector_string: str) -> bool:
    """
    Validates that a vector string contains a valid vector format.
    Prevents SQL injection while allowing scientific notation.
    """
    stripped = vector_string.strip()
    if not (stripped.startswith("[") and stripped.endswith("]")):
        return False

    content = stripped[1:-1]
    if not content.strip():
        return False

    # Validate each number
    parts = content.split(",")
    for part in parts:
        try:
            float(part.strip())
        except ValueError:
            return False

    # Check for SQL injection patterns
    if re.search(r"(DROP|DELETE|INSERT|UPDATE|SELECT|;|--)", vector_string, re.IGNORECASE):
        return False

    return True

def validate_top_k(top_k: any) -> bool:
    """Validates that top_k is a positive integer."""
    try:
        k = int(top_k)
        return k > 0 and k <= 10000  # Reasonable upper limit
    except (ValueError, TypeError):
        return False

def get_iris_dbapi_connection():
    """Get IRIS DBAPI connection using rag-templates pattern."""
    if not IRIS_AVAILABLE:
        raise ImportError("intersystems_irispython not available")

    # Get connection parameters from environment or defaults
    host = os.environ.get("IRIS_HOST", "localhost")
    port = int(os.environ.get("IRIS_PORT", 1973))
    namespace = os.environ.get("IRIS_NAMESPACE", "USER")
    user = os.environ.get("IRIS_USER", "_SYSTEM")
    password = os.environ.get("IRIS_PASSWORD", "SYS")

    try:
        # Use DBAPI interface
        if hasattr(iris, "_DBAPI") and hasattr(iris._DBAPI, "connect"):
            conn = iris._DBAPI.connect(
                hostname=host,
                port=port,
                namespace=namespace,
                username=user,
                password=password,
            )
        elif hasattr(iris, "connect"):
            conn = iris.connect(
                hostname=host,
                port=port,
                namespace=namespace,
                username=user,
                password=password,
            )
        else:
            raise Exception("IRIS module does not have expected connect method")

        # Validate connection
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        cursor.close()

        if result is None:
            conn.close()
            raise Exception("Connection test query failed")

        return conn

    except Exception as e:
        raise Exception(f"IRIS connection failed: {e}")

class StringDataDownloader:
    """Download and parse STRING database files"""

    def __init__(self, config: StringScaleTestConfig):
        self.config = config
        self.data_dir = Path(config.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(__name__)

    def get_string_urls(self) -> Dict[str, str]:
        """Get STRING download URLs"""
        base_url = f"https://stringdb-downloads.org/download"
        organism = self.config.organism
        version = self.config.string_version

        return {
            "protein_info": f"{base_url}/protein.info.{version}/{organism}.protein.info.{version}.txt.gz",
            "protein_links": f"{base_url}/protein.links.{version}/{organism}.protein.links.{version}.txt.gz",
            "protein_aliases": f"{base_url}/protein.aliases.{version}/{organism}.protein.aliases.{version}.txt.gz"
        }

    def download_file(self, url: str, filename: str) -> Path:
        """Download a file if it doesn't exist"""
        filepath = self.data_dir / filename

        if filepath.exists():
            self.logger.info(f"File {filename} already exists, skipping download")
            return filepath

        self.logger.info(f"Downloading {filename} from {url}")
        try:
            urllib.request.urlretrieve(url, filepath)
            self.logger.info(f"Downloaded {filename} ({filepath.stat().st_size / 1024 / 1024:.1f} MB)")
            return filepath
        except Exception as e:
            self.logger.error(f"Failed to download {filename}: {e}")
            raise

    def download_string_data(self) -> Dict[str, Path]:
        """Download all STRING data files"""
        urls = self.get_string_urls()
        files = {}

        for data_type, url in urls.items():
            filename = f"{self.config.organism}.{data_type}.{self.config.string_version}.txt.gz"
            files[data_type] = self.download_file(url, filename)

        return files

class StringProteinProcessor:
    """Process STRING protein data for IRIS"""

    def __init__(self, config: StringScaleTestConfig):
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.protein_data = {}
        self.protein_id_map = {}
        self.interactions = []

    def parse_protein_info(self, filepath: Path) -> Dict[str, Dict]:
        """Parse STRING protein info file"""
        self.logger.info(f"Parsing protein info from {filepath}")
        proteins = {}

        with gzip.open(filepath, 'rt') as f:
            reader = csv.DictReader(f, delimiter='\t')
            for i, row in enumerate(reader):
                if i >= self.config.max_proteins:
                    break

                protein_id = row['#string_protein_id']
                proteins[protein_id] = {
                    'string_id': protein_id,
                    'preferred_name': row['preferred_name'],
                    'protein_size': int(row['protein_size']) if row['protein_size'].isdigit() else 0,
                    'annotation': row['annotation']
                }

                if i % 10000 == 0:
                    self.logger.info(f"Parsed {i} proteins")

        self.logger.info(f"Parsed {len(proteins)} proteins")
        return proteins

    def parse_protein_links(self, filepath: Path, protein_info: Dict) -> List[Tuple]:
        """Parse STRING protein interactions"""
        self.logger.info(f"Parsing protein interactions from {filepath}")
        interactions = []

        with gzip.open(filepath, 'rt') as f:
            reader = csv.DictReader(f, delimiter=' ')
            for i, row in enumerate(reader):
                if i >= self.config.max_interactions:
                    break

                protein1 = row['protein1']
                protein2 = row['protein2']
                combined_score = int(row['combined_score'])

                # Filter by confidence score and ensure proteins exist in our set
                if (combined_score >= self.config.min_score and
                    protein1 in protein_info and
                    protein2 in protein_info):

                    interactions.append((protein1, protein2, combined_score))

                if i % 100000 == 0:
                    self.logger.info(f"Processed {i} interactions, kept {len(interactions)}")

        self.logger.info(f"Parsed {len(interactions)} high-quality interactions")
        return interactions

    def generate_protein_vector(self, protein_info: Dict) -> List[float]:
        """Generate realistic protein embedding based on properties"""
        # Use protein name and size as seed for reproducible vectors
        seed_string = f"{protein_info['preferred_name']}_{protein_info['protein_size']}"
        seed = int(hashlib.md5(seed_string.encode()).hexdigest()[:8], 16)
        np.random.seed(seed)

        # Generate vector with biological structure
        vector = np.random.normal(0, 0.1, self.config.vector_dimension)

        # Add protein size influence (larger proteins cluster differently)
        size_factor = min(protein_info['protein_size'] / 1000.0, 1.0)  # Normalize size
        vector[:64] += np.random.normal(size_factor, 0.1, 64)

        # Add functional clustering based on annotation keywords
        annotation = protein_info['annotation'].lower()
        if 'kinase' in annotation:
            vector[64:128] += np.random.normal(0.3, 0.1, 64)
        elif 'receptor' in annotation:
            vector[128:192] += np.random.normal(0.3, 0.1, 64)
        elif 'transcription' in annotation:
            vector[192:256] += np.random.normal(0.3, 0.1, 64)

        # Normalize
        vector = vector / np.linalg.norm(vector)
        return vector.tolist()

class StringScaleTestSuite:
    def __init__(self, config: StringScaleTestConfig):
        self.config = config
        self.downloader = StringDataDownloader(config)
        self.processor = StringProteinProcessor(config)
        self.setup_logging()
        self.setup_database()

    def setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('string_scale_test.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    def setup_database(self):
        """Connect to IRIS database using rag-templates pattern"""
        try:
            self.conn = get_iris_dbapi_connection()
            self.logger.info("Connected to IRIS database using rag-templates pattern")

            # Create schema if needed
            self.create_schema()

        except Exception as e:
            self.logger.error(f"IRIS database connection failed: {e}")
            raise

    def create_schema(self):
        """Create necessary database schema for scale testing"""
        self.logger.info("Creating/verifying database schema...")
        cursor = self.conn.cursor()

        try:
            # Create tables - IRIS doesn't support IF NOT EXISTS for indexes
            schema_sql = [
                """
                CREATE TABLE IF NOT EXISTS rdf_labels(
                  s      VARCHAR(256) NOT NULL,
                  label  VARCHAR(128) NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS rdf_props(
                  s      VARCHAR(256) NOT NULL,
                  key    VARCHAR(128) NOT NULL,
                  val    VARCHAR(4000)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS rdf_edges(
                  edge_id  BIGINT IDENTITY PRIMARY KEY,
                  s        VARCHAR(256) NOT NULL,
                  p        VARCHAR(128) NOT NULL,
                  o_id     VARCHAR(256) NOT NULL,
                  qualifiers VARCHAR(4000)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS kg_NodeEmbeddings(
                  node_id INT IDENTITY PRIMARY KEY,
                  id   VARCHAR(256),
                  emb  VECTOR(DOUBLE, 768) NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS kg_Documents(
                  doc_id INT IDENTITY PRIMARY KEY,
                  node_id INT NULL,
                  txt VARCHAR(1000000)
                )
                """
            ]

            # Create indexes separately (may fail if they exist, which is OK)
            index_sql = [
                "CREATE INDEX idx_labels_label_s ON rdf_labels(label, s)",
                "CREATE INDEX idx_props_s_key ON rdf_props(s, key)",
                "CREATE INDEX idx_edges_s_p ON rdf_edges(s, p)",
                "CREATE INDEX kg_NodeEmbeddings_id ON kg_NodeEmbeddings(id)",
                # HNSW vector index with ACORN-1 optimization for IRIS 2025.3.0+
                "CREATE INDEX kg_NodeEmbeddings_HNSW ON kg_NodeEmbeddings(emb) AS HNSW(M=16, efConstruction=200, Distance='COSINE') OPTIONS {\"ACORN-1\":1}"
            ]

            # iFind indexes (may require different handling)
            ifind_sql = [
                # Note: iFind syntax may be different in IRIS Community vs Enterprise
                # "CREATE INDEX kg_Documents_TxtIdx ON kg_Documents(txt) AS %iFind.Index.Basic(INDEXOPTION=1, LANGUAGE='en', LOWER=1)"
            ]

            # Create tables first
            for sql in schema_sql:
                try:
                    cursor.execute(sql)
                    self.conn.commit()
                    self.logger.debug(f"Created table/schema")
                except Exception as e:
                    self.logger.debug(f"Table creation step failed (may already exist): {e}")

            # Create indexes (may fail if they already exist)
            for sql in index_sql:
                try:
                    cursor.execute(sql)
                    self.conn.commit()
                    self.logger.debug(f"Created index")
                except Exception as e:
                    self.logger.debug(f"Index creation failed (may already exist): {e}")

            self.logger.info("Schema creation completed")

        except Exception as e:
            self.logger.error(f"Schema creation failed: {e}")
            raise
        finally:
            cursor.close()

    def bulk_insert_proteins(self, protein_data: Dict):
        """Insert protein entities into IRIS"""
        self.logger.info("Inserting proteins into IRIS...")
        cursor = self.conn.cursor()

        try:
            batch_count = 0
            for string_id, info in protein_data.items():
                entity_id = f"protein:{string_id}"

                # Insert label
                cursor.execute(
                    "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
                    [entity_id, "Protein"]
                )

                # Insert properties
                cursor.execute(
                    "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                    [entity_id, "preferred_name", info['preferred_name']]
                )

                cursor.execute(
                    "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                    [entity_id, "protein_size", str(info['protein_size'])]
                )

                cursor.execute(
                    "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                    [entity_id, "annotation", info['annotation'][:1000]]  # Limit annotation length
                )

                batch_count += 1
                if batch_count % self.config.protein_batch_size == 0:
                    self.conn.commit()
                    self.logger.info(f"Inserted {batch_count} proteins")

            self.conn.commit()
            self.logger.info(f"Completed protein insertion: {len(protein_data)} proteins")

        except Exception as e:
            self.logger.error(f"Protein insertion failed: {e}")
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def bulk_insert_interactions(self, interactions: List[Tuple]):
        """Insert protein interactions into IRIS"""
        self.logger.info("Inserting protein interactions...")
        cursor = self.conn.cursor()

        try:
            batch_count = 0
            for protein1, protein2, score in interactions:
                source_id = f"protein:{protein1}"
                target_id = f"protein:{protein2}"

                # Insert interaction edge
                cursor.execute(
                    "INSERT INTO rdf_edges (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)",
                    [source_id, "interacts_with", target_id, json.dumps({"confidence": score})]
                )

                batch_count += 1
                if batch_count % self.config.interaction_batch_size == 0:
                    self.conn.commit()
                    self.logger.info(f"Inserted {batch_count} interactions")

            self.conn.commit()
            self.logger.info(f"Completed interaction insertion: {len(interactions)} interactions")

        except Exception as e:
            self.logger.error(f"Interaction insertion failed: {e}")
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def bulk_insert_protein_embeddings(self, protein_data: Dict):
        """Insert protein embeddings into IRIS using validated string interpolation"""
        self.logger.info("Inserting protein embeddings...")
        cursor = self.conn.cursor()

        try:
            batch_count = 0
            for string_id, info in protein_data.items():
                entity_id = f"protein:{string_id}"
                vector = self.processor.generate_protein_vector(info)
                vector_json = json.dumps(vector)

                # Validate vector string for security (rag-templates pattern)
                if not validate_vector_string(vector_json):
                    raise ValueError(f"Invalid vector format for {entity_id}")

                # Use rag-templates pattern: TO_VECTOR with parameter binding
                sql = "INSERT INTO kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?))"
                cursor.execute(sql, [entity_id, vector_json])

                batch_count += 1
                if batch_count % self.config.embedding_batch_size == 0:
                    self.conn.commit()
                    self.logger.info(f"Inserted {batch_count} embeddings")

            self.conn.commit()
            self.logger.info(f"Completed embedding insertion: {len(protein_data)} embeddings")

        except Exception as e:
            self.logger.error(f"Embedding insertion failed: {e}")
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def bulk_insert_protein_documents(self, protein_data: Dict):
        """Insert protein descriptions as documents for text search"""
        self.logger.info("Inserting protein documents...")
        cursor = self.conn.cursor()

        try:
            for i, (string_id, info) in enumerate(protein_data.items()):
                # Create searchable document from protein info
                document_text = f"Protein {info['preferred_name']} with annotation: {info['annotation']}. Protein size: {info['protein_size']} amino acids."

                cursor.execute(
                    "INSERT INTO kg_Documents (node_id, txt) VALUES (?, ?)",
                    [i, document_text]
                )

                if i % 1000 == 0:
                    self.conn.commit()
                    self.logger.info(f"Inserted {i} protein documents")

            self.conn.commit()
            self.logger.info(f"Completed document insertion: {len(protein_data)} documents")

        except Exception as e:
            self.logger.error(f"Document insertion failed: {e}")
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def benchmark_graph_queries(self, protein_data: Dict) -> Dict:
        """Benchmark graph traversal queries"""
        self.logger.info("Benchmarking graph traversal...")
        cursor = self.conn.cursor()

        times = []
        results_counts = []

        # Sample some proteins for queries
        sample_proteins = random.sample(list(protein_data.keys()),
                                      min(self.config.num_graph_queries, len(protein_data)))

        for i, protein_id in enumerate(sample_proteins):
            entity_id = f"protein:{protein_id}"

            start_time = time.time()
            # Find direct interacting proteins
            cursor.execute("""
                SELECT o_id, qualifiers FROM rdf_edges
                WHERE s = ? AND p = 'interacts_with'
                LIMIT 50
            """, [entity_id])
            results = cursor.fetchall()
            duration = time.time() - start_time

            times.append(duration)
            results_counts.append(len(results))

            if i % 100 == 0:
                self.logger.info(f"Graph query progress: {i}/{len(sample_proteins)}")

        cursor.close()

        return {
            "avg_time": np.mean(times),
            "p95_time": np.percentile(times, 95),
            "p99_time": np.percentile(times, 99),
            "avg_results": np.mean(results_counts),
            "total_queries": len(times)
        }

    def benchmark_vector_queries(self, protein_data: Dict) -> Dict:
        """Benchmark vector similarity search queries"""
        self.logger.info("Benchmarking vector similarity search...")
        cursor = self.conn.cursor()

        times = []
        results_counts = []

        # Sample some proteins for vector queries
        sample_proteins = random.sample(list(protein_data.keys()),
                                      min(100, len(protein_data)))

        for i, protein_id in enumerate(sample_proteins):
            # Generate a random query vector
            query_vector = self.processor.generate_protein_vector(protein_data[protein_id])
            vector_json = json.dumps(query_vector)

            if not validate_vector_string(vector_json):
                continue

            start_time = time.time()
            # Test vector similarity search using rag-templates safe pattern
            sql = f"""
                SELECT TOP 10 id, VECTOR_COSINE(emb, TO_VECTOR('{vector_json}')) AS score
                FROM kg_NodeEmbeddings
                WHERE emb IS NOT NULL
                ORDER BY score DESC
            """
            try:
                cursor.execute(sql)
                results = cursor.fetchall()
                duration = time.time() - start_time
                times.append(duration)
                results_counts.append(len(results))
            except Exception as e:
                self.logger.debug(f"Vector query failed: {e}")

            if i % 20 == 0:
                self.logger.info(f"Vector search progress: {i}/{len(sample_proteins)}")

        cursor.close()

        if times:
            return {
                "avg_time": np.mean(times),
                "p95_time": np.percentile(times, 95),
                "p99_time": np.percentile(times, 99),
                "avg_results": np.mean(results_counts),
                "total_queries": len(times)
            }
        else:
            return {"error": "No successful vector queries"}

    def benchmark_text_queries(self) -> Dict:
        """Benchmark text search queries"""
        self.logger.info("Benchmarking text search...")
        cursor = self.conn.cursor()

        times = []
        results_counts = []

        # Sample text queries
        protein_terms = ["kinase", "protein", "receptor", "enzyme", "binding", "complex"]

        for i, term in enumerate(protein_terms * 10):  # Repeat for more samples
            start_time = time.time()
            try:
                # Simple text search without iFind (for Community Edition compatibility)
                cursor.execute(
                    "SELECT TOP 10 doc_id, txt FROM kg_Documents WHERE txt LIKE ?",
                    [f"%{term}%"]
                )
                results = cursor.fetchall()
                duration = time.time() - start_time
                times.append(duration)
                results_counts.append(len(results))
            except Exception as e:
                self.logger.debug(f"Text query failed: {e}")

            if i % 10 == 0:
                self.logger.info(f"Text search progress: {i}")

        cursor.close()

        if times:
            return {
                "avg_time": np.mean(times),
                "p95_time": np.percentile(times, 95),
                "p99_time": np.percentile(times, 99),
                "avg_results": np.mean(results_counts),
                "total_queries": len(times)
            }
        else:
            return {"error": "No successful text queries"}

    def run_string_scale_test(self):
        """Execute complete STRING-based scale test"""
        start_time = time.time()

        # Phase 1: Download STRING data
        self.logger.info("=== PHASE 1: STRING DATA DOWNLOAD ===")
        download_start = time.time()
        files = self.downloader.download_string_data()
        download_time = time.time() - download_start

        # Phase 2: Parse STRING data
        self.logger.info("=== PHASE 2: STRING DATA PARSING ===")
        parse_start = time.time()
        protein_data = self.processor.parse_protein_info(files['protein_info'])
        interactions = self.processor.parse_protein_links(files['protein_links'], protein_data)
        parse_time = time.time() - parse_start

        # Phase 3: Data ingestion
        self.logger.info("=== PHASE 3: DATA INGESTION ===")
        ingestion_start = time.time()

        # Insert data in parallel where possible
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(self.bulk_insert_proteins, protein_data),
                executor.submit(self.bulk_insert_protein_embeddings, protein_data),
                executor.submit(self.bulk_insert_protein_documents, protein_data)
            ]

            # Wait for these to complete before inserting interactions
            for future in futures:
                future.result()

            # Insert interactions (depends on proteins being inserted)
            self.bulk_insert_interactions(interactions)

        ingestion_time = time.time() - ingestion_start

        # Phase 4: Build indexes
        self.logger.info("=== PHASE 4: INDEX BUILDING ===")
        cursor = self.conn.cursor()
        index_start = time.time()

        # IRIS uses different syntax for index building
        try:
            cursor.execute("BUILD INDEX FOR TABLE kg_NodeEmbeddings")
            self.logger.info("Built indexes for kg_NodeEmbeddings")
        except Exception as e:
            self.logger.debug(f"Index build failed (may not be needed): {e}")

        try:
            cursor.execute("BUILD INDEX FOR TABLE kg_Documents")
            self.logger.info("Built indexes for kg_Documents")
        except Exception as e:
            self.logger.debug(f"Index build failed (may not be needed): {e}")

        cursor.close()
        index_time = time.time() - index_start

        # Phase 5: Performance benchmarks
        self.logger.info("=== PHASE 5: PERFORMANCE BENCHMARKS ===")
        graph_results = self.benchmark_graph_queries(protein_data)
        vector_results = self.benchmark_vector_queries(protein_data)
        text_results = self.benchmark_text_queries()

        total_time = time.time() - start_time

        # Generate comprehensive report
        report = {
            "test_type": "STRING_protein_interaction_scale_test",
            "config": self.config.__dict__,
            "data_stats": {
                "proteins_processed": len(protein_data),
                "interactions_processed": len(interactions),
                "avg_interactions_per_protein": len(interactions) / len(protein_data) if protein_data else 0,
                "confidence_threshold": self.config.min_score
            },
            "timings": {
                "total_time": total_time,
                "download_time": download_time,
                "parse_time": parse_time,
                "ingestion_time": ingestion_time,
                "index_build_time": index_time
            },
            "ingestion_rates": {
                "proteins_per_sec": len(protein_data) / ingestion_time if ingestion_time > 0 else 0,
                "interactions_per_sec": len(interactions) / ingestion_time if ingestion_time > 0 else 0,
                "embeddings_per_sec": len(protein_data) / ingestion_time if ingestion_time > 0 else 0
            },
            "graph_query_performance": graph_results,
            "vector_search_performance": vector_results,
            "text_search_performance": text_results
        }

        # Save report
        with open('string_scale_test_report.json', 'w') as f:
            json.dump(report, f, indent=2)

        self.logger.info("=== STRING SCALE TEST COMPLETE ===")
        self.logger.info(f"Total time: {total_time:.2f} seconds")
        self.logger.info(f"Proteins processed: {len(protein_data):,}")
        self.logger.info(f"Interactions processed: {len(interactions):,}")
        self.logger.info(f"Ingestion rate: {len(protein_data)/ingestion_time:.0f} proteins/sec")
        self.logger.info(f"Graph query avg: {graph_results['avg_time']*1000:.2f}ms")
        if 'avg_time' in vector_results:
            self.logger.info(f"Vector search avg: {vector_results['avg_time']*1000:.2f}ms")
        if 'avg_time' in text_results:
            self.logger.info(f"Text search avg: {text_results['avg_time']*1000:.2f}ms")

        return report

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="STRING Database Scale Test")
    parser.add_argument("--max-proteins", type=int, default=50_000, help="Max proteins to process")
    parser.add_argument("--max-interactions", type=int, default=500_000, help="Max interactions to process")
    parser.add_argument("--min-score", type=int, default=400, help="Minimum STRING confidence score")
    parser.add_argument("--workers", type=int, default=12, help="Max worker threads")

    args = parser.parse_args()

    config = StringScaleTestConfig(
        max_proteins=args.max_proteins,
        max_interactions=args.max_interactions,
        min_score=args.min_score,
        max_workers=args.workers
    )

    test_suite = StringScaleTestSuite(config)
    test_suite.run_string_scale_test()