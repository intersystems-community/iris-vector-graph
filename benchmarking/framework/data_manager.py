#!/usr/bin/env python3
"""
IRIS Graph-AI Benchmarking Data Manager

Manages test dataset generation, loading, and transformation for competitive benchmarking.
Supports synthetic datasets and real-world data sources.
"""

import os
import json
import csv
import numpy as np
import networkx as nx
import pandas as pd
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass
import random
import iris
from neo4j import GraphDatabase
import requests

from benchmark_config import DatasetSpec, DatabaseSystem


@dataclass
class Dataset:
    """Represents a benchmark dataset"""
    name: str
    entities: List[Dict[str, Any]]
    relationships: List[Dict[str, Any]]
    vectors: Dict[str, np.ndarray]  # entity_id -> vector
    metadata: Dict[str, Any]


class BenchmarkDataManager:
    """Manages test dataset generation and loading"""

    def __init__(self):
        self.datasets = {}

    def generate_synthetic_graph(self, spec: DatasetSpec) -> Dataset:
        """Generate synthetic graph data with specified characteristics"""
        print(f"Generating synthetic dataset: {spec.name}")
        print(f"  Entities: {spec.entities:,}")
        print(f"  Relationships: {spec.relationships:,}")
        print(f"  Vector dimensions: {spec.vector_dimensions}")

        # Generate entities
        entities = []
        entity_types = ['protein', 'gene', 'compound', 'pathway', 'disease']

        for i in range(spec.entities):
            entity_id = f"SYNTH_{i:08d}"
            entity_type = random.choice(entity_types)

            entity = {
                'id': entity_id,
                'type': entity_type,
                'label': f"{entity_type.title()} {i}",
                'properties': {
                    'synthetic': True,
                    'confidence': random.uniform(0.7, 1.0),
                    'category': entity_type
                }
            }
            entities.append(entity)

        # Generate relationships based on graph topology
        relationships = []
        relationship_types = ['interacts_with', 'associated_with', 'regulates', 'inhibits', 'activates']

        if spec.name.endswith('_scale_free'):
            # Scale-free network (biological networks)
            G = nx.barabasi_albert_graph(spec.entities, spec.avg_degree // 2)
        elif spec.name.endswith('_small_world'):
            # Small world network (social networks)
            G = nx.watts_strogatz_graph(spec.entities, spec.avg_degree, 0.3)
        else:
            # Random graph
            G = nx.erdos_renyi_graph(spec.entities, spec.relationships / (spec.entities * (spec.entities - 1) / 2))

        edge_count = 0
        for source, target in G.edges():
            if edge_count >= spec.relationships:
                break

            rel_type = random.choice(relationship_types)
            confidence = random.uniform(0.6, 0.99)

            relationship = {
                'source': f"SYNTH_{source:08d}",
                'target': f"SYNTH_{target:08d}",
                'type': rel_type,
                'properties': {
                    'confidence': confidence,
                    'source': 'synthetic_generator',
                    'weight': random.uniform(0.1, 1.0)
                }
            }
            relationships.append(relationship)
            edge_count += 1

        # Generate vector embeddings
        vectors = {}

        # Create clusters for realistic similarity patterns
        num_clusters = min(10, spec.entities // 1000)
        cluster_centers = np.random.randn(num_clusters, spec.vector_dimensions)

        for i, entity in enumerate(entities[:spec.vector_count]):
            # Assign to cluster
            cluster_id = i % num_clusters
            cluster_center = cluster_centers[cluster_id]

            # Add noise around cluster center
            noise = np.random.randn(spec.vector_dimensions) * 0.3
            vector = cluster_center + noise

            # Normalize to unit vector
            vector = vector / np.linalg.norm(vector)

            vectors[entity['id']] = vector

        dataset = Dataset(
            name=spec.name,
            entities=entities,
            relationships=relationships,
            vectors=vectors,
            metadata={
                'type': 'synthetic',
                'spec': spec,
                'graph_properties': {
                    'nodes': len(entities),
                    'edges': len(relationships),
                    'avg_degree': 2 * len(relationships) / len(entities) if entities else 0
                }
            }
        )

        self.datasets[spec.name] = dataset
        print(f"✅ Generated {spec.name}: {len(entities)} entities, {len(relationships)} relationships")
        return dataset

    def load_real_dataset(self, spec: DatasetSpec) -> Dataset:
        """Load and transform real-world datasets"""
        print(f"Loading real dataset: {spec.name} from {spec.data_source}")

        if spec.data_source == "string_database":
            return self._load_string_protein_data(spec)
        elif spec.data_source == "combined_biomedical":
            return self._load_combined_biomedical_data(spec)
        else:
            raise ValueError(f"Unsupported data source: {spec.data_source}")

    def _load_string_protein_data(self, spec: DatasetSpec) -> Dataset:
        """Load STRING protein interaction data"""
        # This would normally load from the existing IRIS database
        # For benchmarking, we'll create a representative dataset

        print("Loading STRING protein data from IRIS database...")

        try:
            conn = iris.connect('localhost', 1973, 'USER', '_SYSTEM', 'SYS')
            cursor = conn.cursor()

            # Load entities
            cursor.execute("SELECT DISTINCT s, label FROM rdf_labels LIMIT ?", [spec.entities])
            entity_rows = cursor.fetchall()

            entities = []
            for entity_id, label in entity_rows:
                entities.append({
                    'id': entity_id,
                    'type': 'protein',
                    'label': label or f"Protein {entity_id}",
                    'properties': {
                        'source': 'string_database',
                        'organism': '9606'  # Human
                    }
                })

            # Load relationships
            cursor.execute("""
                SELECT s, p, o_id, qualifiers
                FROM rdf_edges
                WHERE s IN (SELECT s FROM rdf_labels LIMIT ?)
                LIMIT ?
            """, [spec.entities, spec.relationships])

            relationship_rows = cursor.fetchall()

            relationships = []
            for source, predicate, target, qualifiers in relationship_rows:
                # Parse confidence from qualifiers if available
                confidence = 0.5
                if qualifiers:
                    try:
                        qual_data = json.loads(qualifiers)
                        confidence = float(qual_data.get('confidence', 0.5))
                    except:
                        pass

                relationships.append({
                    'source': source,
                    'target': target,
                    'type': predicate,
                    'properties': {
                        'confidence': confidence,
                        'source': 'string_database'
                    }
                })

            # Load vectors
            cursor.execute("""
                SELECT id, emb
                FROM kg_NodeEmbeddings
                WHERE id IN (SELECT s FROM rdf_labels LIMIT ?)
                LIMIT ?
            """, [spec.entities, spec.vector_count])

            vector_rows = cursor.fetchall()

            vectors = {}
            for entity_id, emb_csv in vector_rows:
                try:
                    vector = np.array([float(x) for x in emb_csv.split(',')])
                    vectors[entity_id] = vector
                except:
                    # Generate synthetic vector if parsing fails
                    vectors[entity_id] = np.random.randn(spec.vector_dimensions)

            cursor.close()
            conn.close()

            dataset = Dataset(
                name=spec.name,
                entities=entities,
                relationships=relationships,
                vectors=vectors,
                metadata={
                    'type': 'real_world',
                    'source': 'string_database',
                    'spec': spec
                }
            )

            self.datasets[spec.name] = dataset
            print(f"✅ Loaded STRING data: {len(entities)} entities, {len(relationships)} relationships")
            return dataset

        except Exception as e:
            print(f"❌ Failed to load STRING data: {e}")
            # Fallback to synthetic data with similar characteristics
            return self.generate_synthetic_graph(spec)

    def _load_combined_biomedical_data(self, spec: DatasetSpec) -> Dataset:
        """Load combined biomedical datasets"""
        # This would combine multiple biomedical sources
        # For now, create a larger synthetic biomedical dataset

        enhanced_spec = DatasetSpec(
            name=spec.name,
            entities=spec.entities,
            relationships=spec.relationships,
            vector_dimensions=spec.vector_dimensions,
            vector_count=spec.vector_count,
            data_source="synthetic_biomedical",
            avg_degree=15  # Higher connectivity for biomedical data
        )

        return self.generate_synthetic_graph(enhanced_spec)

    def load_data_to_iris(self, dataset: Dataset):
        """Load dataset into IRIS database"""
        print(f"Loading {dataset.name} into IRIS...")

        try:
            conn = iris.connect('localhost', 1973, 'USER', '_SYSTEM', 'SYS')
            cursor = conn.cursor()

            # Clear existing benchmark data
            cursor.execute("DELETE FROM rdf_edges WHERE s LIKE 'BENCH_%'")
            cursor.execute("DELETE FROM rdf_labels WHERE s LIKE 'BENCH_%'")
            cursor.execute("DELETE FROM kg_NodeEmbeddings WHERE id LIKE 'BENCH_%'")

            # Load entities
            entity_data = []
            for entity in dataset.entities:
                bench_id = f"BENCH_{entity['id']}"
                entity_data.append((bench_id, entity['label']))

            cursor.executemany(
                "INSERT INTO rdf_labels (s, label) VALUES (?, ?)",
                entity_data
            )

            # Load relationships
            relationship_data = []
            for rel in dataset.relationships:
                bench_source = f"BENCH_{rel['source']}"
                bench_target = f"BENCH_{rel['target']}"
                qualifiers = json.dumps(rel['properties'])

                relationship_data.append((
                    bench_source,
                    rel['type'],
                    bench_target,
                    qualifiers
                ))

            cursor.executemany(
                "INSERT INTO rdf_edges (s, p, o_id, qualifiers) VALUES (?, ?, ?, ?)",
                relationship_data
            )

            # Load vectors
            vector_data = []
            for entity_id, vector in dataset.vectors.items():
                bench_id = f"BENCH_{entity_id}"
                emb_csv = ','.join([str(x) for x in vector])
                vector_data.append((bench_id, bench_id, emb_csv))

            cursor.executemany(
                "INSERT INTO kg_NodeEmbeddings (node_id, id, emb) VALUES (?, ?, ?)",
                vector_data
            )

            conn.commit()
            cursor.close()
            conn.close()

            print(f"✅ Loaded {dataset.name} into IRIS")

        except Exception as e:
            print(f"❌ Failed to load {dataset.name} into IRIS: {e}")

    def load_data_to_neo4j(self, dataset: Dataset, connection_string: str):
        """Load dataset into Neo4j database"""
        print(f"Loading {dataset.name} into Neo4j...")

        try:
            driver = GraphDatabase.driver(connection_string,
                                        auth=("neo4j", "benchmarkpassword"))

            with driver.session() as session:
                # Clear existing benchmark data
                session.run("MATCH (n:BenchmarkEntity) DETACH DELETE n")

                # Load entities in batches
                batch_size = 1000
                for i in range(0, len(dataset.entities), batch_size):
                    batch = dataset.entities[i:i+batch_size]

                    session.run("""
                        UNWIND $entities AS entity
                        CREATE (n:BenchmarkEntity {
                            id: entity.id,
                            type: entity.type,
                            label: entity.label,
                            source: 'benchmark'
                        })
                    """, entities=batch)

                # Load relationships in batches
                for i in range(0, len(dataset.relationships), batch_size):
                    batch = dataset.relationships[i:i+batch_size]

                    session.run("""
                        UNWIND $relationships AS rel
                        MATCH (source:BenchmarkEntity {id: rel.source})
                        MATCH (target:BenchmarkEntity {id: rel.target})
                        CREATE (source)-[r:BENCHMARK_RELATION {
                            type: rel.type,
                            confidence: rel.properties.confidence
                        }]->(target)
                    """, relationships=batch)

                # Create indexes for performance
                session.run("CREATE INDEX benchmark_entity_id IF NOT EXISTS FOR (n:BenchmarkEntity) ON (n.id)")
                session.run("CREATE INDEX benchmark_entity_type IF NOT EXISTS FOR (n:BenchmarkEntity) ON (n.type)")

            driver.close()
            print(f"✅ Loaded {dataset.name} into Neo4j")

        except Exception as e:
            print(f"❌ Failed to load {dataset.name} into Neo4j: {e}")

    def load_data_to_arangodb(self, dataset: Dataset, connection_string: str):
        """Load dataset into ArangoDB"""
        print(f"Loading {dataset.name} into ArangoDB...")

        try:
            # Create collections and load data
            base_url = connection_string
            auth = ('root', 'benchmarkpassword')

            # Create database
            db_name = 'benchmark'
            requests.post(f"{base_url}/_api/database",
                         json={'name': db_name},
                         auth=auth)

            # Create collections
            collections = ['entities', 'relationships']
            for collection in collections:
                requests.post(f"{base_url}/_db/{db_name}/_api/collection",
                             json={'name': collection, 'type': 2 if collection == 'relationships' else 2},
                             auth=auth)

            # Load entities
            entity_docs = []
            for entity in dataset.entities:
                doc = {
                    '_key': entity['id'].replace(':', '_'),
                    'id': entity['id'],
                    'type': entity['type'],
                    'label': entity['label'],
                    'properties': entity['properties']
                }
                entity_docs.append(doc)

            requests.post(f"{base_url}/_db/{db_name}/_api/document/entities",
                         json=entity_docs, auth=auth)

            # Load relationships
            rel_docs = []
            for rel in dataset.relationships:
                doc = {
                    '_from': f"entities/{rel['source'].replace(':', '_')}",
                    '_to': f"entities/{rel['target'].replace(':', '_')}",
                    'type': rel['type'],
                    'properties': rel['properties']
                }
                rel_docs.append(doc)

            requests.post(f"{base_url}/_db/{db_name}/_api/document/relationships",
                         json=rel_docs, auth=auth)

            print(f"✅ Loaded {dataset.name} into ArangoDB")

        except Exception as e:
            print(f"❌ Failed to load {dataset.name} into ArangoDB: {e}")

    def get_dataset(self, name: str) -> Optional[Dataset]:
        """Get dataset by name"""
        return self.datasets.get(name)

    def list_datasets(self) -> List[str]:
        """List available datasets"""
        return list(self.datasets.keys())


if __name__ == "__main__":
    # Test data generation
    from benchmark_config import BenchmarkRequirements

    data_manager = BenchmarkDataManager()

    # Test synthetic data generation
    small_spec = BenchmarkRequirements.DATASET_SPECS['small_synthetic']
    dataset = data_manager.generate_synthetic_graph(small_spec)

    print(f"\nGenerated dataset statistics:")
    print(f"  Entities: {len(dataset.entities)}")
    print(f"  Relationships: {len(dataset.relationships)}")
    print(f"  Vectors: {len(dataset.vectors)}")

    # Test loading into IRIS (if available)
    try:
        data_manager.load_data_to_iris(dataset)
    except Exception as e:
        print(f"IRIS loading test failed: {e}")