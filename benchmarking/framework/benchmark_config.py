#!/usr/bin/env python3
"""
IRIS Graph-AI Competitive Benchmarking Configuration

Defines performance requirements, SLA targets, and test configurations
for competitive benchmarking against Neo4j, Neptune, and other graph databases.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Any
from enum import Enum


class DatabaseSystem(Enum):
    """Supported database systems for benchmarking"""
    IRIS_GRAPH_AI = "iris_graph_ai"
    NEO4J_ENTERPRISE = "neo4j_enterprise"
    NEO4J_COMMUNITY = "neo4j_community"
    AMAZON_NEPTUNE = "amazon_neptune"
    ARANGODB = "arangodb"


class TestCategory(Enum):
    """Benchmark test categories"""
    GRAPH_TRAVERSAL = "graph_traversal"
    VECTOR_SEARCH = "vector_search"
    HYBRID_OPERATIONS = "hybrid_operations"
    SCALE_CONCURRENCY = "scale_concurrency"
    DATA_LOADING = "data_loading"


@dataclass
class PerformanceSLA:
    """Performance SLA requirements for different enterprise tiers"""
    p50_ms: int          # 50th percentile latency in milliseconds
    p95_ms: int          # 95th percentile latency in milliseconds
    p99_ms: int          # 99th percentile latency in milliseconds
    max_users: int       # Maximum concurrent users
    min_throughput: int  # Minimum queries per second


@dataclass
class HardwareSpec:
    """Hardware specification for benchmark environments"""
    cpu_cores: int
    memory_gb: int
    storage_type: str
    storage_gb: int
    network_gbps: int


@dataclass
class DatasetSpec:
    """Dataset specification for benchmarking"""
    name: str
    entities: int
    relationships: int
    vector_dimensions: int
    vector_count: int
    data_source: str
    properties_per_entity: int = 5
    avg_degree: int = 10


@dataclass
class SystemConfig:
    """Database system configuration for benchmarking"""
    system: DatabaseSystem
    version: str
    hardware: HardwareSpec
    config_params: Dict[str, Any]
    docker_image: Optional[str] = None
    cloud_config: Optional[Dict[str, Any]] = None


class BenchmarkRequirements:
    """Central configuration for benchmark requirements and SLAs"""

    # Current System Baseline (from test_working_system.py results)
    IRIS_BASELINE = {
        "text_search_ms": 4,
        "vector_search_ms": 6300,
        "graph_traversal_ms": 10,
        "hybrid_search_ms": 6500,
        "entities": 27219,
        "relationships": 4405,
        "embeddings": 20683
    }

    # Enterprise Performance SLA Targets
    TEXT_SEARCH_SLA = {
        "tier_1": PerformanceSLA(p50_ms=50, p95_ms=100, p99_ms=200, max_users=50, min_throughput=500),
        "tier_2": PerformanceSLA(p50_ms=100, p95_ms=200, p99_ms=400, max_users=200, min_throughput=1000),
        "baseline": PerformanceSLA(p50_ms=500, p95_ms=1000, p99_ms=2000, max_users=10, min_throughput=100)
    }

    VECTOR_SEARCH_SLA = {
        "tier_1": PerformanceSLA(p50_ms=1000, p95_ms=2000, p99_ms=5000, max_users=10, min_throughput=50),
        "tier_2": PerformanceSLA(p50_ms=2000, p95_ms=5000, p99_ms=10000, max_users=50, min_throughput=100),
        "baseline": PerformanceSLA(p50_ms=10000, p95_ms=30000, p99_ms=60000, max_users=5, min_throughput=10)
    }

    GRAPH_TRAVERSAL_SLA = {
        "tier_1": PerformanceSLA(p50_ms=200, p95_ms=500, p99_ms=1000, max_users=50, min_throughput=100),
        "tier_2": PerformanceSLA(p50_ms=500, p95_ms=1000, p99_ms=2000, max_users=200, min_throughput=200),
        "baseline": PerformanceSLA(p50_ms=1000, p95_ms=5000, p99_ms=10000, max_users=10, min_throughput=50)
    }

    HYBRID_SEARCH_SLA = {
        "tier_1": PerformanceSLA(p50_ms=2000, p95_ms=3000, p99_ms=5000, max_users=10, min_throughput=25),
        "tier_2": PerformanceSLA(p50_ms=4000, p95_ms=8000, p99_ms=15000, max_users=50, min_throughput=50),
        "baseline": PerformanceSLA(p50_ms=15000, p95_ms=30000, p99_ms=60000, max_users=5, min_throughput=5)
    }

    # Standard Hardware Configurations
    STANDARD_HARDWARE = HardwareSpec(
        cpu_cores=16,
        memory_gb=64,
        storage_type="nvme_ssd",
        storage_gb=1000,
        network_gbps=10
    )

    LARGE_HARDWARE = HardwareSpec(
        cpu_cores=32,
        memory_gb=128,
        storage_type="nvme_ssd",
        storage_gb=2000,
        network_gbps=10
    )

    # Benchmark Dataset Specifications
    DATASET_SPECS = {
        "small_synthetic": DatasetSpec(
            name="small_synthetic",
            entities=100_000,
            relationships=1_000_000,
            vector_dimensions=768,
            vector_count=100_000,
            data_source="synthetic_generator"
        ),
        "medium_synthetic": DatasetSpec(
            name="medium_synthetic",
            entities=1_000_000,
            relationships=10_000_000,
            vector_dimensions=768,
            vector_count=1_000_000,
            data_source="synthetic_generator"
        ),
        "large_synthetic": DatasetSpec(
            name="large_synthetic",
            entities=10_000_000,
            relationships=100_000_000,
            vector_dimensions=768,
            vector_count=10_000_000,
            data_source="synthetic_generator"
        ),
        "string_proteins": DatasetSpec(
            name="string_proteins",
            entities=20_000,
            relationships=5_000_000,
            vector_dimensions=768,
            vector_count=20_000,
            data_source="string_database"
        ),
        "biomedical_large": DatasetSpec(
            name="biomedical_large",
            entities=2_000_000,
            relationships=50_000_000,
            vector_dimensions=768,
            vector_count=2_000_000,
            data_source="combined_biomedical"
        )
    }

    # System Configurations for Benchmarking
    SYSTEM_CONFIGS = {
        DatabaseSystem.IRIS_GRAPH_AI: SystemConfig(
            system=DatabaseSystem.IRIS_GRAPH_AI,
            version="acorn_1",
            hardware=STANDARD_HARDWARE,
            config_params={
                "optimize_for": "graph_ai",
                "vector_index_type": "hnsw",
                "memory_allocation": "balanced"
            },
            docker_image="intersystems/iris:acorn-1"
        ),
        DatabaseSystem.NEO4J_ENTERPRISE: SystemConfig(
            system=DatabaseSystem.NEO4J_ENTERPRISE,
            version="5.x",
            hardware=STANDARD_HARDWARE,
            config_params={
                "dbms.memory.heap.initial_size": "32g",
                "dbms.memory.heap.max_size": "32g",
                "dbms.memory.pagecache.size": "16g",
                "dbms.transaction.concurrent.maximum": "1000"
            },
            docker_image="neo4j:5-enterprise"
        ),
        DatabaseSystem.AMAZON_NEPTUNE: SystemConfig(
            system=DatabaseSystem.AMAZON_NEPTUNE,
            version="1.2.x",
            hardware=STANDARD_HARDWARE,
            config_params={
                "instance_class": "db.r6g.4xlarge",
                "storage_type": "provisioned_iops",
                "backup_retention": 7
            },
            cloud_config={
                "provider": "aws",
                "region": "us-west-2"
            }
        )
    }

    @classmethod
    def get_competitive_targets(cls) -> Dict[str, Dict[str, float]]:
        """Get competitive performance targets for comparison"""
        return {
            "neo4j_baseline": {
                "graph_traversal_2hop_ms": 50,     # Industry baseline
                "graph_traversal_3hop_ms": 150,    # Industry baseline
                "shortest_path_ms": 200,           # Industry baseline
                "pattern_match_ms": 500,           # Industry baseline
                "concurrent_throughput_qps": 1000   # Industry baseline
            },
            "vector_db_baseline": {
                "knn_search_100k_ms": 50,          # Specialized vector DB
                "knn_search_1m_ms": 200,           # Specialized vector DB
                "vector_index_build_100k_ms": 5000, # Specialized vector DB
                "concurrent_vector_qps": 500        # Specialized vector DB
            },
            "iris_targets": {
                "graph_traversal_advantage": 2.0,   # 2x faster than Neo4j
                "vector_search_parity": 1.0,        # Match specialized DBs
                "hybrid_advantage": 3.0,             # 3x faster than separate systems
                "cost_advantage": 0.7                # 30% lower TCO
            }
        }

    @classmethod
    def validate_current_performance(cls) -> Dict[str, bool]:
        """Validate current IRIS performance against baseline requirements"""
        validation = {}

        # Text search validation
        baseline_text = cls.TEXT_SEARCH_SLA["baseline"]
        validation["text_search_baseline"] = cls.IRIS_BASELINE["text_search_ms"] <= baseline_text.p95_ms

        # Vector search validation
        baseline_vector = cls.VECTOR_SEARCH_SLA["baseline"]
        validation["vector_search_baseline"] = cls.IRIS_BASELINE["vector_search_ms"] <= baseline_vector.p95_ms

        # Graph traversal validation
        baseline_graph = cls.GRAPH_TRAVERSAL_SLA["baseline"]
        validation["graph_traversal_baseline"] = cls.IRIS_BASELINE["graph_traversal_ms"] <= baseline_graph.p95_ms

        # Hybrid search validation
        baseline_hybrid = cls.HYBRID_SEARCH_SLA["baseline"]
        validation["hybrid_search_baseline"] = cls.IRIS_BASELINE["hybrid_search_ms"] <= baseline_hybrid.p95_ms

        return validation


def get_benchmark_config(test_scope: str = "comprehensive") -> Dict[str, Any]:
    """Get benchmark configuration for specified test scope"""

    configs = {
        "quick": {
            "datasets": ["small_synthetic"],
            "systems": [DatabaseSystem.IRIS_GRAPH_AI, DatabaseSystem.NEO4J_COMMUNITY],
            "test_categories": [TestCategory.GRAPH_TRAVERSAL, TestCategory.VECTOR_SEARCH],
            "concurrent_users": [1, 5],
            "iterations": 5
        },
        "standard": {
            "datasets": ["small_synthetic", "medium_synthetic", "string_proteins"],
            "systems": [DatabaseSystem.IRIS_GRAPH_AI, DatabaseSystem.NEO4J_ENTERPRISE],
            "test_categories": [TestCategory.GRAPH_TRAVERSAL, TestCategory.VECTOR_SEARCH,
                              TestCategory.HYBRID_OPERATIONS],
            "concurrent_users": [1, 10, 50],
            "iterations": 10
        },
        "comprehensive": {
            "datasets": ["small_synthetic", "medium_synthetic", "large_synthetic",
                        "string_proteins", "biomedical_large"],
            "systems": [DatabaseSystem.IRIS_GRAPH_AI, DatabaseSystem.NEO4J_ENTERPRISE,
                       DatabaseSystem.AMAZON_NEPTUNE],
            "test_categories": list(TestCategory),
            "concurrent_users": [1, 10, 50, 200],
            "iterations": 20
        }
    }

    return configs.get(test_scope, configs["standard"])


if __name__ == "__main__":
    # Validate current system performance
    validation = BenchmarkRequirements.validate_current_performance()

    print("IRIS Graph-AI Performance Validation Against Baseline Requirements")
    print("=" * 70)

    for test, passed in validation.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} {test}")

    overall = all(validation.values())
    print(f"\nOverall Baseline Validation: {'✅ PASS' if overall else '❌ FAIL'}")

    if overall:
        print("\n🎯 System meets baseline requirements for competitive benchmarking")
    else:
        print("\n⚠️  System needs optimization before competitive benchmarking")

    # Show competitive targets
    targets = BenchmarkRequirements.get_competitive_targets()
    print(f"\nCompetitive Performance Targets:")
    for category, metrics in targets.items():
        print(f"\n{category}:")
        for metric, value in metrics.items():
            print(f"  {metric}: {value}")