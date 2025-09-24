#!/usr/bin/env python3
"""
Large-scale IRIS biomedical knowledge graph test using PMC documents
Leverages the PMC corpus from rag-templates for realistic biomedical content
"""

import json
import time
import random
import numpy as np
import pyodbc
import xml.etree.ElementTree as ET
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from dataclasses import dataclass
from typing import List, Dict, Tuple, Set
import argparse
import hashlib

# Import from rag-templates if available
import sys
sys.path.append('/Users/tdyar/ws/rag-templates')

try:
    from data.pmc_processor import PMCProcessor
    PMC_AVAILABLE = True
except ImportError:
    PMC_AVAILABLE = False
    logging.warning("PMC processor not available, using synthetic data")

@dataclass
class PMCScaleTestConfig:
    # Data scale
    target_entities: int = 500_000     # Target nodes extracted from PMC
    target_documents: int = 100_000    # PMC documents to process
    max_pmc_files: int = 1000          # Limit PMC files to process

    # Vector configuration
    vector_dimension: int = 768        # OpenAI ada-002 compatible

    # Entity extraction parameters
    min_entity_frequency: int = 5      # Minimum occurrences to include entity
    max_entities_per_doc: int = 100    # Limit entities per document

    # Performance test parameters
    num_vector_queries: int = 2000     # Vector search tests
    num_text_queries: int = 2000       # Text search tests
    num_hybrid_queries: int = 1000     # Hybrid search tests
    num_graph_queries: int = 1000      # Graph traversal tests

    # Batch sizes for ingestion
    entity_batch_size: int = 5_000
    edge_batch_size: int = 25_000
    embedding_batch_size: int = 2_000
    document_batch_size: int = 2_000

    # Concurrency
    max_workers: int = 12

    # PMC data paths
    pmc_sample_dir: str = "/Users/tdyar/ws/rag-templates/data/sample_10_docs"
    pmc_downloaded_dir: str = "/Users/tdyar/ws/rag-templates/data/downloaded_pmc_docs"

class BiomedicalEntityExtractor:
    """Extract biomedical entities from PMC XML content"""

    def __init__(self):
        # Biomedical entity patterns (simplified NER)
        self.entity_patterns = {
            'gene': r'\b[A-Z][A-Z0-9]{2,10}\b',  # Gene names (e.g., TP53, BRCA1)
            'protein': r'\b[A-Z][a-z]+\d*\b(?:\s+protein)?',  # Protein names
            'disease': r'\b(?:cancer|tumor|carcinoma|lymphoma|leukemia|syndrome|disease)\b',
            'drug': r'\b[A-Z][a-z]+(?:mab|nib|tide|cin|ine|zole)\b',  # Drug suffixes
            'pathway': r'\b[A-Z][a-zA-Z\s]+pathway\b',
            'cell_type': r'\b[A-Z][a-z]+\s+cells?\b',
            'tissue': r'\b[a-z]+\s+tissue\b',
        }

        # Relationship patterns
        self.relation_patterns = [
            (r'(\w+)\s+(?:regulates|controls|modulates)\s+(\w+)', 'regulates'),
            (r'(\w+)\s+(?:interacts with|binds to)\s+(\w+)', 'interacts_with'),
            (r'(\w+)\s+(?:causes|induces|triggers)\s+(\w+)', 'causes'),
            (r'(\w+)\s+(?:treats|inhibits|blocks)\s+(\w+)', 'treats'),
            (r'(\w+)\s+(?:expressed in|found in)\s+(\w+)', 'expressed_in'),
        ]

    def extract_entities_from_text(self, text: str, doc_id: str) -> Dict[str, Set[str]]:
        """Extract biomedical entities from text"""
        entities = {entity_type: set() for entity_type in self.entity_patterns}

        # Clean text
        text = re.sub(r'[^\w\s]', ' ', text.lower())

        for entity_type, pattern in self.entity_patterns.items():
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if len(match) > 2:  # Filter very short matches
                    entities[entity_type].add(match.lower().strip())

        return entities

    def extract_relationships_from_text(self, text: str) -> List[Tuple[str, str, str]]:
        """Extract relationships from text"""
        relationships = []
        text = text.lower()

        for pattern, relation_type in self.relation_patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for source, target in matches:
                if len(source) > 2 and len(target) > 2:
                    relationships.append((source.strip(), relation_type, target.strip()))

        return relationships

class PMCScaleTestSuite:
    def __init__(self, config: PMCScaleTestConfig):
        self.config = config
        self.extractor = BiomedicalEntityExtractor()
        self.entity_frequency = {}
        self.entity_id_map = {}
        self.next_entity_id = 0
        self.setup_logging()
        self.setup_database()

    def setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('pmc_scale_test.log'),
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

    def get_pmc_files(self) -> List[Path]:
        """Get list of PMC XML files to process"""
        pmc_files = []

        # Check sample directory first
        sample_dir = Path(self.config.pmc_sample_dir)
        if sample_dir.exists():
            pmc_files.extend(list(sample_dir.glob("*.xml")))
            self.logger.info(f"Found {len(pmc_files)} files in sample directory")

        # Check downloaded directory if we need more
        if len(pmc_files) < self.config.max_pmc_files:
            downloaded_dir = Path(self.config.pmc_downloaded_dir)
            if downloaded_dir.exists():
                additional_files = list(downloaded_dir.glob("*.xml"))
                pmc_files.extend(additional_files[:self.config.max_pmc_files - len(pmc_files)])
                self.logger.info(f"Added {len(additional_files)} files from downloaded directory")

        return pmc_files[:self.config.max_pmc_files]

    def parse_pmc_xml(self, xml_file: Path) -> Dict:
        """Parse PMC XML file and extract content"""
        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()

            # Extract title
            title_elem = root.find('.//article-title')
            title = title_elem.text if title_elem is not None else f"Document {xml_file.stem}"

            # Extract abstract
            abstract_elem = root.find('.//abstract')
            abstract = ""
            if abstract_elem is not None:
                abstract_parts = []
                for p in abstract_elem.findall('.//p'):
                    if p.text:
                        abstract_parts.append(p.text)
                abstract = " ".join(abstract_parts)

            # Extract body text
            body_elem = root.find('.//body')
            body_text = ""
            if body_elem is not None:
                body_parts = []
                for p in body_elem.findall('.//p'):
                    if p.text:
                        body_parts.append(p.text)
                body_text = " ".join(body_parts)

            # Combine all text
            full_text = f"{title}. {abstract}. {body_text}"

            return {
                'pmcid': xml_file.stem,
                'title': title,
                'abstract': abstract,
                'body': body_text,
                'full_text': full_text[:50000]  # Limit text length
            }

        except Exception as e:
            self.logger.warning(f"Failed to parse {xml_file}: {e}")
            return None

    def process_pmc_documents(self) -> List[Dict]:
        """Process all PMC documents and extract entities"""
        self.logger.info("Processing PMC documents...")
        pmc_files = self.get_pmc_files()

        if not pmc_files:
            self.logger.error("No PMC files found!")
            return []

        processed_docs = []

        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            future_to_file = {executor.submit(self.parse_pmc_xml, pmc_file): pmc_file
                            for pmc_file in pmc_files}

            for future in as_completed(future_to_file):
                pmc_file = future_to_file[future]
                try:
                    doc_data = future.result()
                    if doc_data:
                        # Extract entities
                        entities = self.extractor.extract_entities_from_text(
                            doc_data['full_text'], doc_data['pmcid']
                        )

                        # Extract relationships
                        relationships = self.extractor.extract_relationships_from_text(
                            doc_data['full_text']
                        )

                        doc_data['entities'] = entities
                        doc_data['relationships'] = relationships
                        processed_docs.append(doc_data)

                        # Update entity frequency
                        for entity_type, entity_set in entities.items():
                            for entity in entity_set:
                                key = f"{entity_type}:{entity}"
                                self.entity_frequency[key] = self.entity_frequency.get(key, 0) + 1

                        if len(processed_docs) % 100 == 0:
                            self.logger.info(f"Processed {len(processed_docs)} documents")

                except Exception as e:
                    self.logger.error(f"Error processing {pmc_file}: {e}")

        self.logger.info(f"Processed {len(processed_docs)} PMC documents")
        self.logger.info(f"Found {len(self.entity_frequency)} unique entities")

        return processed_docs

    def assign_entity_ids(self):
        """Assign numeric IDs to frequent entities"""
        frequent_entities = {
            entity: freq for entity, freq in self.entity_frequency.items()
            if freq >= self.config.min_entity_frequency
        }

        self.logger.info(f"Using {len(frequent_entities)} frequent entities (freq >= {self.config.min_entity_frequency})")

        for entity in frequent_entities:
            self.entity_id_map[entity] = self.next_entity_id
            self.next_entity_id += 1

    def generate_realistic_vector(self, entity_name: str) -> List[float]:
        """Generate realistic 768D embedding based on entity name"""
        # Use entity name as seed for reproducible vectors
        seed = int(hashlib.md5(entity_name.encode()).hexdigest()[:8], 16)
        np.random.seed(seed)

        # Generate vector with some structure
        vector = np.random.normal(0, 0.1, self.config.vector_dimension)

        # Add semantic clustering based on entity type
        if 'gene:' in entity_name or 'protein:' in entity_name:
            vector[:64] += np.random.normal(0.5, 0.1, 64)  # Gene/protein cluster
        elif 'disease:' in entity_name:
            vector[64:128] += np.random.normal(0.5, 0.1, 64)  # Disease cluster
        elif 'drug:' in entity_name:
            vector[128:192] += np.random.normal(0.5, 0.1, 64)  # Drug cluster

        # Normalize
        vector = vector / np.linalg.norm(vector)
        return vector.tolist()

    def bulk_insert_pmc_entities(self, processed_docs: List[Dict]):
        """Insert entities extracted from PMC documents"""
        self.logger.info("Inserting PMC entities...")
        cursor = self.conn.cursor()

        try:
            batch_count = 0
            for entity, entity_id in self.entity_id_map.items():
                entity_type, entity_name = entity.split(':', 1)
                full_entity_id = f"entity:{entity_id}"

                # Insert label
                cursor.execute(
                    "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
                    full_entity_id, entity_type
                )

                # Insert name property
                cursor.execute(
                    "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                    full_entity_id, "name", entity_name
                )

                # Insert frequency as property
                cursor.execute(
                    "INSERT INTO rdf_props (s, key, val) VALUES (?, ?, ?)",
                    full_entity_id, "frequency", str(self.entity_frequency[entity])
                )

                batch_count += 1
                if batch_count % self.config.entity_batch_size == 0:
                    self.conn.commit()
                    self.logger.info(f"Inserted {batch_count} entities")

            self.conn.commit()
            self.logger.info(f"Completed entity insertion: {len(self.entity_id_map)} entities")

        except Exception as e:
            self.logger.error(f"Entity insertion failed: {e}")
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def bulk_insert_pmc_relationships(self, processed_docs: List[Dict]):
        """Insert relationships extracted from PMC documents"""
        self.logger.info("Inserting PMC relationships...")
        cursor = self.conn.cursor()

        try:
            relationship_count = 0

            for doc in processed_docs:
                for source, relation_type, target in doc['relationships']:
                    source_key = None
                    target_key = None

                    # Find matching entities
                    for entity_type in ['gene', 'protein', 'disease', 'drug', 'pathway', 'cell_type']:
                        source_entity = f"{entity_type}:{source}"
                        target_entity = f"{entity_type}:{target}"

                        if source_entity in self.entity_id_map and target_entity in self.entity_id_map:
                            source_id = f"entity:{self.entity_id_map[source_entity]}"
                            target_id = f"entity:{self.entity_id_map[target_entity]}"

                            cursor.execute(
                                "INSERT INTO rdf_edges (s, p, o_id) VALUES (?, ?, ?)",
                                source_id, relation_type, target_id
                            )
                            relationship_count += 1
                            break

                if relationship_count % self.config.edge_batch_size == 0:
                    self.conn.commit()
                    self.logger.info(f"Inserted {relationship_count} relationships")

            self.conn.commit()
            self.logger.info(f"Completed relationship insertion: {relationship_count} relationships")

        except Exception as e:
            self.logger.error(f"Relationship insertion failed: {e}")
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def bulk_insert_pmc_embeddings(self):
        """Insert embeddings for PMC entities"""
        self.logger.info("Inserting PMC embeddings...")
        cursor = self.conn.cursor()

        try:
            batch_count = 0
            for entity, entity_id in self.entity_id_map.items():
                full_entity_id = f"entity:{entity_id}"
                vector = self.generate_realistic_vector(entity)
                vector_json = json.dumps(vector)

                cursor.execute(
                    "INSERT INTO kg_NodeEmbeddings (id, emb) VALUES (?, TO_VECTOR(?))",
                    full_entity_id, vector_json
                )

                batch_count += 1
                if batch_count % self.config.embedding_batch_size == 0:
                    self.conn.commit()
                    self.logger.info(f"Inserted {batch_count} embeddings")

            self.conn.commit()
            self.logger.info(f"Completed embedding insertion: {len(self.entity_id_map)} embeddings")

        except Exception as e:
            self.logger.error(f"Embedding insertion failed: {e}")
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def bulk_insert_pmc_documents(self, processed_docs: List[Dict]):
        """Insert PMC documents for text search"""
        self.logger.info("Inserting PMC documents...")
        cursor = self.conn.cursor()

        try:
            for i, doc in enumerate(processed_docs):
                # Use a representative entity as node_id if available
                node_id = None
                for entity_type, entity_set in doc['entities'].items():
                    for entity in entity_set:
                        entity_key = f"{entity_type}:{entity}"
                        if entity_key in self.entity_id_map:
                            node_id = self.entity_id_map[entity_key]
                            break
                    if node_id:
                        break

                cursor.execute(
                    "INSERT INTO kg_Documents (node_id, txt) VALUES (?, ?)",
                    node_id, doc['full_text']
                )

                if i % self.config.document_batch_size == 0:
                    self.conn.commit()
                    self.logger.info(f"Inserted {i} documents")

            self.conn.commit()
            self.logger.info(f"Completed document insertion: {len(processed_docs)} documents")

        except Exception as e:
            self.logger.error(f"Document insertion failed: {e}")
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def run_pmc_scale_test(self):
        """Execute complete PMC-based scale test"""
        start_time = time.time()

        # Phase 1: Process PMC documents
        self.logger.info("=== PHASE 1: PMC DOCUMENT PROCESSING ===")
        processing_start = time.time()
        processed_docs = self.process_pmc_documents()

        if not processed_docs:
            self.logger.error("No documents processed, aborting test")
            return

        self.assign_entity_ids()
        processing_time = time.time() - processing_start

        # Phase 2: Data ingestion
        self.logger.info("=== PHASE 2: DATA INGESTION ===")
        ingestion_start = time.time()

        self.bulk_insert_pmc_entities(processed_docs)
        self.bulk_insert_pmc_relationships(processed_docs)
        self.bulk_insert_pmc_embeddings()
        self.bulk_insert_pmc_documents(processed_docs)

        ingestion_time = time.time() - ingestion_start

        # Phase 3: Build indexes
        self.logger.info("=== PHASE 3: INDEX BUILDING ===")
        cursor = self.conn.cursor()
        index_start = time.time()
        cursor.execute("BUILD INDEX ON TABLE kg_NodeEmbeddings")
        cursor.execute("BUILD INDEX ON TABLE kg_Documents")
        cursor.execute("TUNE TABLE kg_NodeEmbeddings")
        cursor.execute("TUNE TABLE kg_Documents")
        cursor.close()
        index_time = time.time() - index_start

        total_time = time.time() - start_time

        # Generate comprehensive report
        report = {
            "test_type": "PMC_biomedical_scale_test",
            "config": self.config.__dict__,
            "data_stats": {
                "documents_processed": len(processed_docs),
                "entities_extracted": len(self.entity_id_map),
                "total_entity_occurrences": sum(self.entity_frequency.values()),
                "avg_entities_per_doc": len(self.entity_id_map) / len(processed_docs) if processed_docs else 0
            },
            "timings": {
                "total_time": total_time,
                "processing_time": processing_time,
                "ingestion_time": ingestion_time,
                "index_build_time": index_time
            },
            "ingestion_rates": {
                "entities_per_sec": len(self.entity_id_map) / ingestion_time if ingestion_time > 0 else 0,
                "documents_per_sec": len(processed_docs) / ingestion_time if ingestion_time > 0 else 0,
                "embeddings_per_sec": len(self.entity_id_map) / ingestion_time if ingestion_time > 0 else 0
            }
        }

        # Save report
        with open('pmc_scale_test_report.json', 'w') as f:
            json.dump(report, f, indent=2)

        self.logger.info("=== PMC SCALE TEST COMPLETE ===")
        self.logger.info(f"Total time: {total_time:.2f} seconds")
        self.logger.info(f"Documents processed: {len(processed_docs)}")
        self.logger.info(f"Entities extracted: {len(self.entity_id_map)}")
        self.logger.info(f"Processing rate: {len(processed_docs)/processing_time:.1f} docs/sec")
        self.logger.info(f"Ingestion rate: {len(self.entity_id_map)/ingestion_time:.0f} entities/sec")

        return report

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PMC Biomedical Graph Scale Test")
    parser.add_argument("--max-docs", type=int, default=1000, help="Max PMC documents to process")
    parser.add_argument("--min-freq", type=int, default=5, help="Minimum entity frequency")
    parser.add_argument("--workers", type=int, default=12, help="Max worker threads")

    args = parser.parse_args()

    config = PMCScaleTestConfig(
        max_pmc_files=args.max_docs,
        min_entity_frequency=args.min_freq,
        max_workers=args.workers
    )

    test_suite = PMCScaleTestSuite(config)
    test_suite.run_pmc_scale_test()