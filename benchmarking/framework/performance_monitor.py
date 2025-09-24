#!/usr/bin/env python3
"""
IRIS Graph-AI Benchmarking Performance Monitor

Comprehensive performance measurement and monitoring for competitive benchmarking.
Measures latency, throughput, resource utilization, and system metrics.
"""

import time
import psutil
import threading
import statistics
import docker
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
import json
import iris
from neo4j import GraphDatabase
import requests

from benchmark_config import DatabaseSystem
from query_generator import Query


@dataclass
class QueryMetrics:
    """Metrics for a single query execution"""
    query_name: str
    execution_time_ms: float
    result_count: int
    memory_usage_mb: float
    cpu_usage_percent: float
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class PerformanceMetrics:
    """Comprehensive performance metrics"""
    system: DatabaseSystem
    query_metrics: List[QueryMetrics] = field(default_factory=list)

    # Latency statistics
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0

    # Throughput metrics
    queries_per_second: float = 0.0
    total_queries: int = 0
    successful_queries: int = 0
    failed_queries: int = 0

    # Resource utilization
    peak_memory_mb: float = 0.0
    avg_memory_mb: float = 0.0
    peak_cpu_percent: float = 0.0
    avg_cpu_percent: float = 0.0

    # Test duration
    test_duration_seconds: float = 0.0
    start_time: datetime = field(default_factory=datetime.now)
    end_time: Optional[datetime] = None


@dataclass
class ResourceMetrics:
    """System resource utilization metrics"""
    timestamp: datetime
    memory_usage_mb: float
    cpu_usage_percent: float
    disk_io_read_mb: float
    disk_io_write_mb: float
    network_io_mb: float


class PerformanceMonitor:
    """Comprehensive performance measurement and monitoring"""

    def __init__(self):
        self.docker_client = docker.from_env()
        self.monitoring_active = False
        self.resource_metrics = []
        self.monitor_thread = None

    def measure_query_performance(self, query: Query, system: DatabaseSystem,
                                 connection_config: Dict[str, Any]) -> QueryMetrics:
        """Measure single query performance"""

        # Start resource monitoring
        start_memory = psutil.virtual_memory().used / (1024 * 1024)
        start_cpu = psutil.cpu_percent()

        start_time = time.time()
        error = None
        result_count = 0

        try:
            if system == DatabaseSystem.IRIS_GRAPH_AI:
                result_count = self._execute_iris_query(query, connection_config)
            elif system in [DatabaseSystem.NEO4J_ENTERPRISE, DatabaseSystem.NEO4J_COMMUNITY]:
                result_count = self._execute_neo4j_query(query, connection_config)
            elif system == DatabaseSystem.ARANGODB:
                result_count = self._execute_arangodb_query(query, connection_config)
            else:
                raise ValueError(f"Unsupported system: {system}")

        except Exception as e:
            error = str(e)

        end_time = time.time()
        execution_time_ms = (end_time - start_time) * 1000

        # Measure resource usage
        end_memory = psutil.virtual_memory().used / (1024 * 1024)
        end_cpu = psutil.cpu_percent()

        memory_usage = end_memory - start_memory
        cpu_usage = max(end_cpu - start_cpu, 0)  # Prevent negative values

        return QueryMetrics(
            query_name=query.name,
            execution_time_ms=execution_time_ms,
            result_count=result_count,
            memory_usage_mb=memory_usage,
            cpu_usage_percent=cpu_usage,
            error=error
        )

    def measure_concurrent_performance(self, queries: List[Query],
                                     users: int, system: DatabaseSystem,
                                     connection_config: Dict[str, Any]) -> PerformanceMetrics:
        """Measure concurrent query performance"""

        print(f"Starting concurrent performance test: {users} users, {len(queries)} queries")

        start_time = datetime.now()
        all_metrics = []

        # Start system resource monitoring
        self._start_resource_monitoring(system)

        # Prepare worker threads
        threads = []
        results_lock = threading.Lock()

        def worker_thread(thread_id: int):
            """Worker thread for concurrent query execution"""
            thread_metrics = []

            for query in queries:
                try:
                    metric = self.measure_query_performance(query, system, connection_config)
                    thread_metrics.append(metric)
                except Exception as e:
                    error_metric = QueryMetrics(
                        query_name=query.name,
                        execution_time_ms=0,
                        result_count=0,
                        memory_usage_mb=0,
                        cpu_usage_percent=0,
                        error=str(e)
                    )
                    thread_metrics.append(error_metric)

            with results_lock:
                all_metrics.extend(thread_metrics)

        # Start concurrent workers
        for i in range(users):
            thread = threading.Thread(target=worker_thread, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all workers to complete
        for thread in threads:
            thread.join()

        end_time = datetime.now()

        # Stop resource monitoring
        self._stop_resource_monitoring()

        # Calculate performance metrics
        performance = self._calculate_performance_metrics(
            system, all_metrics, start_time, end_time
        )

        return performance

    def measure_system_resources(self, system: DatabaseSystem,
                               container_name: str = None) -> ResourceMetrics:
        """Measure system resource utilization"""

        timestamp = datetime.now()

        # System-wide metrics
        memory_usage = psutil.virtual_memory().used / (1024 * 1024)
        cpu_usage = psutil.cpu_percent(interval=1)

        # Disk I/O metrics
        disk_io = psutil.disk_io_counters()
        disk_read_mb = disk_io.read_bytes / (1024 * 1024) if disk_io else 0
        disk_write_mb = disk_io.write_bytes / (1024 * 1024) if disk_io else 0

        # Network I/O metrics
        net_io = psutil.net_io_counters()
        network_mb = (net_io.bytes_sent + net_io.bytes_recv) / (1024 * 1024) if net_io else 0

        # Container-specific metrics if available
        if container_name:
            try:
                container = self.docker_client.containers.get(container_name)
                stats = container.stats(stream=False)

                # Container memory usage
                memory_stats = stats['memory_stats']
                if 'usage' in memory_stats:
                    memory_usage = memory_stats['usage'] / (1024 * 1024)

                # Container CPU usage
                cpu_stats = stats['cpu_stats']
                precpu_stats = stats['precpu_stats']

                if 'cpu_usage' in cpu_stats and 'cpu_usage' in precpu_stats:
                    cpu_delta = cpu_stats['cpu_usage']['total_usage'] - precpu_stats['cpu_usage']['total_usage']
                    system_delta = cpu_stats['system_cpu_usage'] - precpu_stats['system_cpu_usage']

                    if system_delta > 0:
                        cpu_usage = (cpu_delta / system_delta) * len(cpu_stats['cpu_usage']['percpu_usage']) * 100

            except Exception as e:
                print(f"Warning: Could not get container metrics: {e}")

        return ResourceMetrics(
            timestamp=timestamp,
            memory_usage_mb=memory_usage,
            cpu_usage_percent=cpu_usage,
            disk_io_read_mb=disk_read_mb,
            disk_io_write_mb=disk_write_mb,
            network_io_mb=network_mb
        )

    def _execute_iris_query(self, query: Query, connection_config: Dict[str, Any]) -> int:
        """Execute query on IRIS system"""
        conn = iris.connect('localhost', 1973, 'USER', '_SYSTEM', 'SYS')

        try:
            if query.iris_python:
                # Execute Python operator
                sys.path.insert(0, '/Users/tdyar/ws/graph-ai/python')
                from iris_graph_operators import IRISGraphOperators

                operators = IRISGraphOperators(conn)

                # Parse Python code and execute
                if 'kg_KNN_VEC' in query.iris_python:
                    params = query.parameters or {}
                    result = operators.kg_KNN_VEC(
                        params.get('query_vector', '[]'),
                        k=params.get('k', 10),
                        label_filter=params.get('label_filter')
                    )
                elif 'kg_RRF_FUSE' in query.iris_python:
                    params = query.parameters or {}
                    result = operators.kg_RRF_FUSE(
                        k=params.get('k', 10),
                        query_vector=params.get('query_vector'),
                        query_text=params.get('query_text')
                    )
                else:
                    result = []

                return len(result)

            elif query.iris_sql:
                # Execute SQL query
                cursor = conn.cursor()

                if query.parameters:
                    # Convert parameters to list for IRIS
                    param_values = list(query.parameters.values())
                    cursor.execute(query.iris_sql, param_values)
                else:
                    cursor.execute(query.iris_sql)

                results = cursor.fetchall()
                cursor.close()
                return len(results)

        finally:
            conn.close()

        return 0

    def _execute_neo4j_query(self, query: Query, connection_config: Dict[str, Any]) -> int:
        """Execute query on Neo4j system"""
        driver = GraphDatabase.driver(
            connection_config.get('uri', 'bolt://localhost:7687'),
            auth=("neo4j", "benchmarkpassword")
        )

        try:
            with driver.session() as session:
                if query.neo4j_cypher:
                    result = session.run(query.neo4j_cypher, query.parameters or {})
                    records = list(result)
                    return len(records)
        finally:
            driver.close()

        return 0

    def _execute_arangodb_query(self, query: Query, connection_config: Dict[str, Any]) -> int:
        """Execute query on ArangoDB system"""
        base_url = connection_config.get('url', 'http://localhost:8529')
        auth = ('root', 'benchmarkpassword')

        if query.arangodb_aql:
            response = requests.post(
                f"{base_url}/_db/benchmark/_api/cursor",
                json={'query': query.arangodb_aql, 'bindVars': query.parameters or {}},
                auth=auth
            )

            if response.status_code == 201:
                data = response.json()
                return len(data.get('result', []))

        return 0

    def _start_resource_monitoring(self, system: DatabaseSystem):
        """Start background resource monitoring"""
        self.monitoring_active = True
        self.resource_metrics = []

        def monitor_resources():
            container_name = None
            if system == DatabaseSystem.IRIS_GRAPH_AI:
                container_name = "iris_benchmark"
            elif system in [DatabaseSystem.NEO4J_ENTERPRISE, DatabaseSystem.NEO4J_COMMUNITY]:
                container_name = "neo4j_benchmark"
            elif system == DatabaseSystem.ARANGODB:
                container_name = "arangodb_benchmark"

            while self.monitoring_active:
                try:
                    metrics = self.measure_system_resources(system, container_name)
                    self.resource_metrics.append(metrics)
                    time.sleep(1)  # Sample every second
                except Exception as e:
                    print(f"Resource monitoring error: {e}")
                    time.sleep(1)

        self.monitor_thread = threading.Thread(target=monitor_resources)
        self.monitor_thread.start()

    def _stop_resource_monitoring(self):
        """Stop background resource monitoring"""
        self.monitoring_active = False
        if self.monitor_thread:
            self.monitor_thread.join()

    def _calculate_performance_metrics(self, system: DatabaseSystem,
                                     query_metrics: List[QueryMetrics],
                                     start_time: datetime, end_time: datetime) -> PerformanceMetrics:
        """Calculate comprehensive performance metrics"""

        performance = PerformanceMetrics(
            system=system,
            query_metrics=query_metrics,
            start_time=start_time,
            end_time=end_time
        )

        # Filter successful queries for latency calculations
        successful_times = [
            m.execution_time_ms for m in query_metrics if m.error is None
        ]

        if successful_times:
            # Latency statistics
            performance.avg_latency_ms = statistics.mean(successful_times)
            performance.p50_latency_ms = statistics.median(successful_times)

            sorted_times = sorted(successful_times)
            performance.p95_latency_ms = sorted_times[int(0.95 * len(sorted_times))]
            performance.p99_latency_ms = sorted_times[int(0.99 * len(sorted_times))]

        # Throughput metrics
        performance.total_queries = len(query_metrics)
        performance.successful_queries = len(successful_times)
        performance.failed_queries = performance.total_queries - performance.successful_queries

        duration_seconds = (end_time - start_time).total_seconds()
        performance.test_duration_seconds = duration_seconds

        if duration_seconds > 0:
            performance.queries_per_second = performance.successful_queries / duration_seconds

        # Resource utilization from monitoring
        if self.resource_metrics:
            memory_values = [m.memory_usage_mb for m in self.resource_metrics]
            cpu_values = [m.cpu_usage_percent for m in self.resource_metrics]

            performance.peak_memory_mb = max(memory_values)
            performance.avg_memory_mb = statistics.mean(memory_values)
            performance.peak_cpu_percent = max(cpu_values)
            performance.avg_cpu_percent = statistics.mean(cpu_values)

        return performance

    def export_metrics_to_json(self, metrics: PerformanceMetrics, filename: str):
        """Export performance metrics to JSON file"""

        # Convert metrics to serializable format
        data = {
            'system': metrics.system.value,
            'test_summary': {
                'total_queries': metrics.total_queries,
                'successful_queries': metrics.successful_queries,
                'failed_queries': metrics.failed_queries,
                'test_duration_seconds': metrics.test_duration_seconds,
                'queries_per_second': metrics.queries_per_second
            },
            'latency_metrics': {
                'avg_latency_ms': metrics.avg_latency_ms,
                'p50_latency_ms': metrics.p50_latency_ms,
                'p95_latency_ms': metrics.p95_latency_ms,
                'p99_latency_ms': metrics.p99_latency_ms
            },
            'resource_metrics': {
                'peak_memory_mb': metrics.peak_memory_mb,
                'avg_memory_mb': metrics.avg_memory_mb,
                'peak_cpu_percent': metrics.peak_cpu_percent,
                'avg_cpu_percent': metrics.avg_cpu_percent
            },
            'individual_queries': [
                {
                    'query_name': qm.query_name,
                    'execution_time_ms': qm.execution_time_ms,
                    'result_count': qm.result_count,
                    'memory_usage_mb': qm.memory_usage_mb,
                    'cpu_usage_percent': qm.cpu_usage_percent,
                    'error': qm.error,
                    'timestamp': qm.timestamp.isoformat()
                }
                for qm in metrics.query_metrics
            ]
        }

        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)

        print(f"✅ Performance metrics exported to {filename}")


if __name__ == "__main__":
    # Test performance monitoring
    from query_generator import Query
    from benchmark_config import DatabaseSystem, TestCategory

    monitor = PerformanceMonitor()

    # Test single query measurement
    test_query = Query(
        category=TestCategory.GRAPH_TRAVERSAL,
        name="test_entity_lookup",
        description="Test entity lookup",
        iris_sql="SELECT TOP 10 s, label FROM rdf_labels",
        performance_target_ms=100
    )

    try:
        metrics = monitor.measure_query_performance(
            test_query,
            DatabaseSystem.IRIS_GRAPH_AI,
            {}
        )

        print(f"Query Performance Test:")
        print(f"  Query: {metrics.query_name}")
        print(f"  Execution time: {metrics.execution_time_ms:.2f}ms")
        print(f"  Result count: {metrics.result_count}")
        print(f"  Memory usage: {metrics.memory_usage_mb:.2f}MB")
        print(f"  Error: {metrics.error}")

    except Exception as e:
        print(f"Performance test failed: {e}")