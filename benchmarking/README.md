# IRIS Graph-AI Competitive Benchmarking Framework

Enterprise-grade competitive benchmarking infrastructure for validating IRIS Graph-AI performance against established graph database solutions (Neo4j, Amazon Neptune, ArangoDB).

## Overview

This framework provides comprehensive competitive analysis with:

- **Automated Environment Setup**: Docker-based deployment of IRIS and competitor systems
- **Standardized Test Datasets**: Synthetic and real-world datasets with configurable scale
- **Cross-Platform Query Translation**: Equivalent queries across different database systems
- **Performance Monitoring**: Latency, throughput, and resource utilization metrics
- **Statistical Analysis**: Multiple iterations with percentile-based reporting
- **Executive Reporting**: Business-ready competitive analysis and recommendations

## Quick Start

### Prerequisites

```bash
# Install Python dependencies
pip install docker psutil neo4j numpy pandas networkx

# Ensure Docker is running
docker --version

# Verify IRIS access
python -c "import iris; print('IRIS available')"
```

### Run Quick Validation

```bash
# Validate IRIS baseline performance
python run_competitive_benchmark.py --validate-only

# Run quick competitive benchmark (IRIS vs Neo4j Community)
python run_competitive_benchmark.py --scope quick
```

### Standard Benchmark Suite

```bash
# Run standard benchmark (recommended)
python run_competitive_benchmark.py --scope standard

# Custom systems selection
python run_competitive_benchmark.py --systems iris_graph_ai,neo4j_enterprise

# Custom output directory
python run_competitive_benchmark.py --output ./enterprise_benchmark_results
```

## Benchmark Scopes

### Quick Scope (20 minutes)
- **Systems**: IRIS Graph-AI vs Neo4j Community
- **Datasets**: Small synthetic (100K entities, 1M relationships)
- **Tests**: Graph traversal, vector search
- **Users**: 1, 5 concurrent
- **Purpose**: Rapid validation and framework testing

### Standard Scope (2-4 hours)
- **Systems**: IRIS Graph-AI vs Neo4j Enterprise
- **Datasets**: Small synthetic, medium synthetic, STRING proteins
- **Tests**: Graph traversal, vector search, hybrid operations
- **Users**: 1, 10, 50 concurrent
- **Purpose**: Comprehensive competitive analysis

### Comprehensive Scope (8-12 hours)
- **Systems**: IRIS Graph-AI vs Neo4j Enterprise vs Amazon Neptune
- **Datasets**: All synthetic scales + biomedical datasets
- **Tests**: All categories including scale/concurrency
- **Users**: 1, 10, 50, 200 concurrent
- **Purpose**: Enterprise decision-making validation

## Performance Requirements and SLAs

### Enterprise Performance Targets

#### Tier 1 Enterprise (Small)
- **Scale**: 100K-1M entities, 1M-10M relationships
- **Text Search**: <100ms (p95), 500+ QPS
- **Vector Search**: <2s (p95), 50+ QPS
- **Graph Traversal**: <500ms (p95) for 3-hop
- **Hybrid Search**: <3s (p95), 25+ QPS

#### Tier 2 Enterprise (Mid-Market)
- **Scale**: 1M-10M entities, 10M-100M relationships
- **Text Search**: <200ms (p95), 1000+ QPS
- **Vector Search**: <5s (p95), 100+ QPS
- **Graph Traversal**: <1s (p95) for 3-hop
- **Hybrid Search**: <8s (p95), 50+ QPS

### Current IRIS Baseline
Based on validation tests:
- **Text Search**: ~4ms (✅ Excellent)
- **Vector Search**: ~6.3s (⚠️ Needs optimization)
- **Graph Traversal**: <10ms (✅ Excellent)
- **Hybrid Search**: ~6.5s (⚠️ Optimization target)

## Framework Architecture

### Core Components

```
benchmarking/
├── framework/
│   ├── benchmark_config.py      # Performance SLAs and system configs
│   ├── environment_manager.py   # Docker environment setup/teardown
│   ├── data_manager.py         # Dataset generation and loading
│   ├── query_generator.py      # Cross-platform query translation
│   ├── performance_monitor.py  # Metrics collection and analysis
│   └── benchmark_runner.py     # Test orchestration
├── docs/                       # Design documents and specifications
├── results/                    # Benchmark results and reports
└── run_competitive_benchmark.py # Main entry point
```

### Test Categories

#### 1. Graph Traversal Performance
- Entity lookup and relationship queries
- Multi-hop neighborhood expansion (2-hop, 3-hop)
- Shortest path algorithms
- Pattern matching and triangle detection
- Hub entity identification

#### 2. Vector Similarity Performance
- K-NN search with varying K values (5, 10, 25, 50)
- Range similarity queries with thresholds
- Filtered vector search by entity type
- Batch vector operations

#### 3. Hybrid Operations (IRIS Strength)
- Graph-RAG style queries (vector + graph traversal)
- Multi-modal search (text + vector + graph)
- RRF (Reciprocal Rank Fusion) operations
- Real-time analytics patterns

#### 4. Scale and Concurrency
- Concurrent user simulation (1, 10, 50, 200 users)
- Memory scaling characteristics
- Storage efficiency comparisons
- Data loading performance

## Dataset Specifications

### Synthetic Datasets
- **Small**: 100K entities, 1M relationships, 768D vectors
- **Medium**: 1M entities, 10M relationships, 768D vectors
- **Large**: 10M entities, 100M relationships, 768D vectors
- **Topology**: Scale-free, small-world, and random networks

### Real-World Datasets
- **STRING Proteins**: Existing biomedical data (~20K proteins)
- **Biomedical Large**: Combined datasets (2M entities, 50M relationships)
- **Enterprise Simulation**: Synthetic business data patterns

## Results and Analysis

### Automated Reports

#### Executive Summary (`executive_summary_*.md`)
- Competitive positioning assessment
- Performance gap analysis
- Go/no-go decision recommendations
- ROI analysis for optimization investments

#### Technical Report (`benchmark_report_*.json`)
- Detailed performance metrics
- Statistical analysis (p50, p95, p99 latencies)
- Resource utilization patterns
- Individual query performance breakdown

### Key Metrics

#### Performance Comparison
- **Latency Ratios**: IRIS vs competitor response times
- **Throughput Ratios**: Queries per second comparisons
- **Memory Efficiency**: Resource usage patterns
- **Storage Overhead**: Disk space requirements

#### Competitive Analysis
- Market readiness assessment
- Optimization priority identification
- Technical roadmap validation
- Cost-benefit analysis

## Success Criteria

### Go/No-Go Decision Framework

#### ✅ GO (Enterprise Expansion)
- Meet minimum viable performance thresholds
- Demonstrate clear advantages in 2+ areas
- Identify 3+ customer scenarios where IRIS wins
- Validate technical roadmap to competitive performance

#### ❌ NO-GO (Focus on Biomedical Excellence)
- Fail to meet minimum performance thresholds
- No clear competitive advantages identified
- Technical roadmap to competitiveness unclear
- Market validation shows insufficient differentiation

### Performance Thresholds

#### Minimum Viable (Phase 0)
- Graph Traversal: Within 5x of Neo4j
- Vector Search: Within 3x of specialized solutions
- Hybrid Operations: Demonstrable advantage over separate systems

#### Competitive Success (Phase 1)
- Graph Traversal: Within 2x of Neo4j
- Vector Search: Within 2x of specialized solutions
- Hybrid Operations: 2x+ faster than separate systems
- Total Cost: 20%+ lower TCO than Neo4j

#### Market Leadership (Phase 2)
- Match or exceed Neo4j in key scenarios
- Match specialized vector database performance
- 3x+ advantage in hybrid operations
- Unique capabilities not available elsewhere

## Advanced Usage

### Custom Dataset Testing

```python
from framework.data_manager import BenchmarkDataManager
from framework.benchmark_config import DatasetSpec

# Create custom dataset specification
custom_spec = DatasetSpec(
    name="custom_enterprise",
    entities=500_000,
    relationships=5_000_000,
    vector_dimensions=768,
    vector_count=500_000,
    data_source="synthetic_generator",
    avg_degree=20
)

# Generate and benchmark
data_manager = BenchmarkDataManager()
dataset = data_manager.generate_synthetic_graph(custom_spec)
```

### Performance Monitoring

```python
from framework.performance_monitor import PerformanceMonitor
from framework.query_generator import Query

monitor = PerformanceMonitor()

# Custom query performance measurement
custom_query = Query(
    category=TestCategory.GRAPH_TRAVERSAL,
    name="custom_pattern",
    iris_sql="SELECT ... FROM rdf_edges WHERE ...",
    neo4j_cypher="MATCH (n)-[r]-(m) WHERE ... RETURN ..."
)

metrics = monitor.measure_query_performance(
    custom_query,
    DatabaseSystem.IRIS_GRAPH_AI,
    connection_config
)
```

### Result Analysis

```python
from framework.benchmark_runner import BenchmarkRunner

runner = BenchmarkRunner()
report = runner.run_competitive_benchmark("standard")

# Access detailed results
for system_name, results in report.system_results.items():
    for result in results:
        print(f"{system_name}: {result.performance_metrics.avg_latency_ms}ms")

# Export custom analysis
runner.performance_monitor.export_metrics_to_json(
    report.system_results['iris_graph_ai'][0].performance_metrics,
    "custom_analysis.json"
)
```

## Troubleshooting

### Common Issues

#### Docker Environment Setup
```bash
# Cleanup stuck containers
docker ps -a | grep benchmark | awk '{print $1}' | xargs docker rm -f

# Free up ports
docker stop $(docker ps -q --filter "publish=1973")
docker stop $(docker ps -q --filter "publish=7687")
```

#### IRIS Connection Issues
```bash
# Verify IRIS container
docker logs iris_benchmark

# Test connection manually
python -c "
import iris
conn = iris.connect('localhost', 1973, 'USER', '_SYSTEM', 'SYS')
print('IRIS connection successful')
"
```

#### Memory/Resource Issues
```bash
# Monitor system resources
docker stats

# Increase Docker memory limits in Docker Desktop
# Recommended: 8GB+ RAM, 4+ CPU cores
```

### Performance Optimization

#### Vector Search Optimization
- Ensure HNSW indexes are built properly
- Verify vector storage format (CSV vs JSON)
- Consider parallel processing for large result sets

#### Graph Query Optimization
- Use appropriate join strategies in SQL
- Leverage IRIS-specific optimizations
- Consider pre-computed relationship caches

## Contributing

### Adding New Database Systems

1. **Update `benchmark_config.py`**:
   ```python
   class DatabaseSystem(Enum):
       NEW_SYSTEM = "new_system"
   ```

2. **Implement in `environment_manager.py`**:
   ```python
   def setup_new_system_environment(self, config):
       # Container setup logic
   ```

3. **Add query translation in `query_generator.py`**:
   ```python
   def _generate_basic_graph_queries(self):
       # Add new_system_query field to Query objects
   ```

4. **Implement execution in `performance_monitor.py`**:
   ```python
   def _execute_new_system_query(self, query, connection_config):
       # Query execution logic
   ```

### Adding New Test Categories

1. **Define category in `benchmark_config.py`**
2. **Implement query generation in `query_generator.py`**
3. **Add performance targets and SLAs**
4. **Update documentation**

## References

- [IRIS Vector Functions Documentation](https://docs.intersystems.com/iris20234/csp/docbook/DocBook.UI.Page.cls?KEY=RSQL_vector)
- [Neo4j Performance Tuning Guide](https://neo4j.com/docs/operations-manual/current/performance/)
- [Amazon Neptune Best Practices](https://docs.aws.amazon.com/neptune/latest/userguide/best-practices.html)
- [Graph Database Benchmarking Standards](https://ldbcouncil.org/benchmarks/snb/)

---

**Note**: This benchmarking framework provides objective performance comparison but should be complemented with business requirements analysis, total cost of ownership evaluation, and specific use case validation for enterprise decision-making.