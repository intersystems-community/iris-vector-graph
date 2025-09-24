#!/usr/bin/env python3
"""
IRIS Graph-AI Benchmarking Query Generator

Generates equivalent queries across different database systems for competitive benchmarking.
Creates standardized test queries for graph traversal, vector search, and hybrid operations.
"""

import json
import random
import numpy as np
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass
from enum import Enum

from benchmark_config import TestCategory
from data_manager import Dataset


@dataclass
class Query:
    """Represents a benchmark query"""
    category: TestCategory
    name: str
    description: str
    iris_sql: Optional[str] = None
    iris_python: Optional[str] = None
    neo4j_cypher: Optional[str] = None
    arangodb_aql: Optional[str] = None
    parameters: Dict[str, Any] = None
    expected_result_type: str = "list"
    performance_target_ms: int = 1000


@dataclass
class QuerySet:
    """Collection of related queries for a test scenario"""
    name: str
    category: TestCategory
    queries: List[Query]
    dataset_requirements: Dict[str, Any]
    setup_queries: List[Query] = None


class QueryGenerator:
    """Generates equivalent queries across different systems"""

    def __init__(self, dataset: Dataset):
        self.dataset = dataset
        self.entity_samples = random.sample(dataset.entities, min(100, len(dataset.entities)))
        self.relationship_samples = random.sample(dataset.relationships, min(100, len(dataset.relationships)))

    def generate_graph_queries(self) -> List[QuerySet]:
        """Generate graph traversal queries"""
        graph_queries = []

        # 1. Basic relationship queries
        basic_queries = self._generate_basic_graph_queries()
        graph_queries.append(QuerySet(
            name="basic_graph_operations",
            category=TestCategory.GRAPH_TRAVERSAL,
            queries=basic_queries,
            dataset_requirements={"min_entities": 1000, "min_relationships": 5000}
        ))

        # 2. Multi-hop traversal queries
        traversal_queries = self._generate_traversal_queries()
        graph_queries.append(QuerySet(
            name="multi_hop_traversal",
            category=TestCategory.GRAPH_TRAVERSAL,
            queries=traversal_queries,
            dataset_requirements={"min_entities": 5000, "min_relationships": 25000}
        ))

        # 3. Pattern matching queries
        pattern_queries = self._generate_pattern_queries()
        graph_queries.append(QuerySet(
            name="pattern_matching",
            category=TestCategory.GRAPH_TRAVERSAL,
            queries=pattern_queries,
            dataset_requirements={"min_entities": 10000, "min_relationships": 50000}
        ))

        return graph_queries

    def generate_vector_queries(self) -> List[QuerySet]:
        """Generate vector similarity queries"""
        vector_queries = []

        # 1. K-NN queries
        knn_queries = self._generate_knn_queries()
        vector_queries.append(QuerySet(
            name="knn_similarity_search",
            category=TestCategory.VECTOR_SEARCH,
            queries=knn_queries,
            dataset_requirements={"min_vectors": 10000}
        ))

        # 2. Range queries
        range_queries = self._generate_range_queries()
        vector_queries.append(QuerySet(
            name="range_similarity_search",
            category=TestCategory.VECTOR_SEARCH,
            queries=range_queries,
            dataset_requirements={"min_vectors": 50000}
        ))

        # 3. Filtered vector queries
        filtered_queries = self._generate_filtered_vector_queries()
        vector_queries.append(QuerySet(
            name="filtered_vector_search",
            category=TestCategory.VECTOR_SEARCH,
            queries=filtered_queries,
            dataset_requirements={"min_vectors": 25000, "min_entities": 25000}
        ))

        return vector_queries

    def generate_hybrid_queries(self) -> List[QuerySet]:
        """Generate hybrid graph+vector queries"""
        hybrid_queries = []

        # 1. Graph-RAG style queries
        graph_rag_queries = self._generate_graph_rag_queries()
        hybrid_queries.append(QuerySet(
            name="graph_rag_operations",
            category=TestCategory.HYBRID_OPERATIONS,
            queries=graph_rag_queries,
            dataset_requirements={"min_entities": 10000, "min_vectors": 10000}
        ))

        # 2. Multi-modal search
        multimodal_queries = self._generate_multimodal_queries()
        hybrid_queries.append(QuerySet(
            name="multimodal_search",
            category=TestCategory.HYBRID_OPERATIONS,
            queries=multimodal_queries,
            dataset_requirements={"min_entities": 25000, "min_vectors": 25000}
        ))

        return hybrid_queries

    def _generate_basic_graph_queries(self) -> List[Query]:
        """Generate basic graph operation queries"""
        queries = []

        # Entity lookup by ID
        sample_entity = random.choice(self.entity_samples)
        queries.append(Query(
            category=TestCategory.GRAPH_TRAVERSAL,
            name="entity_lookup_by_id",
            description="Look up entity by ID",
            iris_sql="SELECT s, label FROM rdf_labels WHERE s = ?",
            neo4j_cypher="MATCH (n:BenchmarkEntity {id: $entity_id}) RETURN n.id, n.label",
            parameters={"entity_id": sample_entity['id']},
            performance_target_ms=10
        ))

        # Find all relationships for an entity
        queries.append(Query(
            category=TestCategory.GRAPH_TRAVERSAL,
            name="entity_relationships",
            description="Find all relationships for an entity",
            iris_sql="SELECT s, p, o_id FROM rdf_edges WHERE s = ? OR o_id = ?",
            neo4j_cypher="MATCH (n:BenchmarkEntity {id: $entity_id})-[r]-(m) RETURN n.id, type(r), m.id",
            parameters={"entity_id": sample_entity['id']},
            performance_target_ms=50
        ))

        # Count relationships by type
        queries.append(Query(
            category=TestCategory.GRAPH_TRAVERSAL,
            name="relationship_type_counts",
            description="Count relationships by type",
            iris_sql="SELECT p, COUNT(*) as count FROM rdf_edges GROUP BY p ORDER BY count DESC",
            neo4j_cypher="MATCH ()-[r]->() RETURN type(r) as rel_type, count(r) as count ORDER BY count DESC",
            performance_target_ms=100
        ))

        return queries

    def _generate_traversal_queries(self) -> List[Query]:
        """Generate multi-hop traversal queries"""
        queries = []

        sample_entity = random.choice(self.entity_samples)

        # 2-hop neighborhood
        queries.append(Query(
            category=TestCategory.GRAPH_TRAVERSAL,
            name="two_hop_neighborhood",
            description="Find all entities within 2 hops",
            iris_sql="""
                SELECT DISTINCT e2.o_id as target_entity
                FROM rdf_edges e1
                JOIN rdf_edges e2 ON e1.o_id = e2.s
                WHERE e1.s = ?
            """,
            neo4j_cypher="""
                MATCH (start:BenchmarkEntity {id: $entity_id})-[*1..2]-(target)
                RETURN DISTINCT target.id
            """,
            parameters={"entity_id": sample_entity['id']},
            performance_target_ms=200
        ))

        # 3-hop neighborhood
        queries.append(Query(
            category=TestCategory.GRAPH_TRAVERSAL,
            name="three_hop_neighborhood",
            description="Find all entities within 3 hops",
            iris_sql="""
                SELECT DISTINCT e3.o_id as target_entity
                FROM rdf_edges e1
                JOIN rdf_edges e2 ON e1.o_id = e2.s
                JOIN rdf_edges e3 ON e2.o_id = e3.s
                WHERE e1.s = ?
            """,
            neo4j_cypher="""
                MATCH (start:BenchmarkEntity {id: $entity_id})-[*1..3]-(target)
                RETURN DISTINCT target.id
            """,
            parameters={"entity_id": sample_entity['id']},
            performance_target_ms=500
        ))

        # Shortest path between two entities
        entity1 = random.choice(self.entity_samples)
        entity2 = random.choice(self.entity_samples)
        queries.append(Query(
            category=TestCategory.GRAPH_TRAVERSAL,
            name="shortest_path",
            description="Find shortest path between two entities",
            iris_python="operators.kg_GRAPH_PATH(source, 'interacts_with', target)",
            neo4j_cypher="""
                MATCH path = shortestPath((start:BenchmarkEntity {id: $source})-[*]-(end:BenchmarkEntity {id: $target}))
                RETURN path
            """,
            parameters={"source": entity1['id'], "target": entity2['id']},
            performance_target_ms=1000
        ))

        return queries

    def _generate_pattern_queries(self) -> List[Query]:
        """Generate pattern matching queries"""
        queries = []

        # Triangle patterns (A -> B -> C -> A)
        queries.append(Query(
            category=TestCategory.GRAPH_TRAVERSAL,
            name="triangle_pattern",
            description="Find triangle patterns in the graph",
            iris_sql="""
                SELECT e1.s, e1.o_id, e2.o_id
                FROM rdf_edges e1
                JOIN rdf_edges e2 ON e1.o_id = e2.s
                JOIN rdf_edges e3 ON e2.o_id = e3.s
                WHERE e3.o_id = e1.s
                LIMIT 100
            """,
            neo4j_cypher="""
                MATCH (a:BenchmarkEntity)-[]->(b:BenchmarkEntity)-[]->(c:BenchmarkEntity)-[]->(a)
                RETURN a.id, b.id, c.id
                LIMIT 100
            """,
            performance_target_ms=2000
        ))

        # Hub entities (high degree nodes)
        queries.append(Query(
            category=TestCategory.GRAPH_TRAVERSAL,
            name="hub_entities",
            description="Find entities with high connectivity (hubs)",
            iris_sql="""
                SELECT s, COUNT(*) as degree
                FROM rdf_edges
                GROUP BY s
                ORDER BY degree DESC
                LIMIT 10
            """,
            neo4j_cypher="""
                MATCH (n:BenchmarkEntity)-[r]-()
                RETURN n.id, count(r) as degree
                ORDER BY degree DESC
                LIMIT 10
            """,
            performance_target_ms=500
        ))

        return queries

    def _generate_knn_queries(self) -> List[Query]:
        """Generate K-NN vector queries"""
        queries = []

        # Get sample vectors
        sample_vectors = list(self.dataset.vectors.values())[:10]

        for k in [5, 10, 25, 50]:
            for i, vector in enumerate(sample_vectors[:3]):
                vector_json = json.dumps(vector.tolist())

                queries.append(Query(
                    category=TestCategory.VECTOR_SEARCH,
                    name=f"knn_search_k{k}_query{i+1}",
                    description=f"K-NN search with k={k}",
                    iris_python=f"operators.kg_KNN_VEC('{vector_json}', k={k})",
                    parameters={"query_vector": vector_json, "k": k},
                    performance_target_ms=2000 if k <= 10 else 5000
                ))

        return queries

    def _generate_range_queries(self) -> List[Query]:
        """Generate range similarity queries"""
        queries = []

        sample_vectors = list(self.dataset.vectors.values())[:5]

        for threshold in [0.7, 0.8, 0.9]:
            for i, vector in enumerate(sample_vectors):
                vector_json = json.dumps(vector.tolist())

                queries.append(Query(
                    category=TestCategory.VECTOR_SEARCH,
                    name=f"range_search_threshold{threshold}_query{i+1}",
                    description=f"Range search with similarity threshold {threshold}",
                    iris_sql=f"""
                        SELECT id, VECTOR_COSINE(TO_VECTOR(emb), TO_VECTOR(?)) as similarity
                        FROM kg_NodeEmbeddings
                        WHERE VECTOR_COSINE(TO_VECTOR(emb), TO_VECTOR(?)) >= {threshold}
                        ORDER BY similarity DESC
                    """,
                    parameters={"query_vector": vector_json, "threshold": threshold},
                    performance_target_ms=3000
                ))

        return queries

    def _generate_filtered_vector_queries(self) -> List[Query]:
        """Generate filtered vector search queries"""
        queries = []

        sample_vectors = list(self.dataset.vectors.values())[:3]
        entity_types = list(set([e['type'] for e in self.entity_samples]))

        for vector in sample_vectors:
            for entity_type in entity_types[:2]:
                vector_json = json.dumps(vector.tolist())

                queries.append(Query(
                    category=TestCategory.VECTOR_SEARCH,
                    name=f"filtered_knn_{entity_type}",
                    description=f"K-NN search filtered by entity type: {entity_type}",
                    iris_python=f"operators.kg_KNN_VEC('{vector_json}', k=10, label_filter='{entity_type}')",
                    parameters={"query_vector": vector_json, "entity_type": entity_type},
                    performance_target_ms=3000
                ))

        return queries

    def _generate_graph_rag_queries(self) -> List[Query]:
        """Generate Graph-RAG style queries"""
        queries = []

        sample_vectors = list(self.dataset.vectors.values())[:3]
        search_terms = ["protein", "gene", "interaction", "pathway"]

        for i, vector in enumerate(sample_vectors):
            for term in search_terms:
                vector_json = json.dumps(vector.tolist())

                queries.append(Query(
                    category=TestCategory.HYBRID_OPERATIONS,
                    name=f"graph_rag_query_{term}_{i+1}",
                    description=f"Graph-RAG query: vector + text search for '{term}'",
                    iris_python=f"operators.kg_RRF_FUSE(k=10, query_vector='{vector_json}', query_text='{term}')",
                    parameters={"query_vector": vector_json, "query_text": term},
                    performance_target_ms=5000
                ))

        return queries

    def _generate_multimodal_queries(self) -> List[Query]:
        """Generate multi-modal search queries"""
        queries = []

        sample_entities = self.entity_samples[:5]
        sample_vectors = list(self.dataset.vectors.values())[:3]

        for entity in sample_entities:
            for vector in sample_vectors:
                vector_json = json.dumps(vector.tolist())

                queries.append(Query(
                    category=TestCategory.HYBRID_OPERATIONS,
                    name=f"multimodal_expand_entity_{entity['id'][:8]}",
                    description=f"Find similar entities and expand their graph neighborhood",
                    iris_python=f"""
                        # First find similar entities
                        similar = operators.kg_KNN_VEC('{vector_json}', k=5)
                        # Then expand graph neighborhood
                        expanded = []
                        for entity_id, score in similar:
                            neighbors = operators.kg_GRAPH_PATH(entity_id, 'interacts_with', None)
                            expanded.extend(neighbors)
                        return expanded
                    """,
                    parameters={"start_entity": entity['id'], "query_vector": vector_json},
                    performance_target_ms=8000
                ))

        return queries

    def generate_scale_queries(self) -> List[QuerySet]:
        """Generate queries for scale and concurrency testing"""
        scale_queries = []

        # Concurrent read queries
        concurrent_queries = []
        for i in range(10):
            sample_entity = random.choice(self.entity_samples)
            concurrent_queries.append(Query(
                category=TestCategory.SCALE_CONCURRENCY,
                name=f"concurrent_entity_lookup_{i}",
                description=f"Concurrent entity lookup #{i}",
                iris_sql="SELECT s, label FROM rdf_labels WHERE s = ?",
                neo4j_cypher="MATCH (n:BenchmarkEntity {id: $entity_id}) RETURN n.id, n.label",
                parameters={"entity_id": sample_entity['id']},
                performance_target_ms=100
            ))

        scale_queries.append(QuerySet(
            name="concurrent_operations",
            category=TestCategory.SCALE_CONCURRENCY,
            queries=concurrent_queries,
            dataset_requirements={"min_entities": 10000}
        ))

        return scale_queries

    def get_all_query_sets(self) -> List[QuerySet]:
        """Get all generated query sets"""
        all_queries = []
        all_queries.extend(self.generate_graph_queries())
        all_queries.extend(self.generate_vector_queries())
        all_queries.extend(self.generate_hybrid_queries())
        all_queries.extend(self.generate_scale_queries())
        return all_queries


if __name__ == "__main__":
    # Test query generation
    from data_manager import BenchmarkDataManager
    from benchmark_config import BenchmarkRequirements

    # Generate test dataset
    data_manager = BenchmarkDataManager()
    spec = BenchmarkRequirements.DATASET_SPECS['small_synthetic']
    dataset = data_manager.generate_synthetic_graph(spec)

    # Generate queries
    query_gen = QueryGenerator(dataset)
    all_query_sets = query_gen.get_all_query_sets()

    print(f"Generated {len(all_query_sets)} query sets:")
    for query_set in all_query_sets:
        print(f"  {query_set.name}: {len(query_set.queries)} queries ({query_set.category.value})")

    # Show sample query
    if all_query_sets:
        sample_query = all_query_sets[0].queries[0]
        print(f"\nSample query: {sample_query.name}")
        print(f"  Description: {sample_query.description}")
        print(f"  IRIS SQL: {sample_query.iris_sql}")
        print(f"  Neo4j Cypher: {sample_query.neo4j_cypher}")
        print(f"  Performance target: {sample_query.performance_target_ms}ms")