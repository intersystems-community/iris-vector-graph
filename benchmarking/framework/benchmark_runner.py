#!/usr/bin/env python3
"""
IRIS Graph-AI Competitive Benchmarking Runner

Orchestrates complete benchmark execution including environment setup,
data loading, query execution, and results analysis.
"""

import os
import json
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
import concurrent.futures

from benchmark_config import (
    BenchmarkRequirements, DatabaseSystem, TestCategory,
    get_benchmark_config
)
from environment_manager import BenchmarkEnvironment, EnvironmentStatus
from data_manager import BenchmarkDataManager, Dataset
from query_generator import QueryGenerator, QuerySet
from performance_monitor import PerformanceMonitor, PerformanceMetrics


@dataclass
class BenchmarkResult:
    """Results for a single system benchmark"""
    system: DatabaseSystem
    dataset_name: str
    performance_metrics: PerformanceMetrics
    query_set_results: Dict[str, PerformanceMetrics] = field(default_factory=dict)
    environment_setup_time: float = 0.0
    data_loading_time: float = 0.0
    total_benchmark_time: float = 0.0
    errors: List[str] = field(default_factory=list)


@dataclass
class BenchmarkReport:
    """Complete benchmark report with all results"""
    benchmark_config: Dict[str, Any]
    start_time: datetime
    end_time: Optional[datetime] = None
    total_duration_hours: float = 0.0

    # Results by system
    system_results: Dict[str, List[BenchmarkResult]] = field(default_factory=dict)

    # Summary statistics
    summary: Dict[str, Any] = field(default_factory=dict)

    # Competitive analysis
    competitive_analysis: Dict[str, Any] = field(default_factory=dict)


class BenchmarkRunner:
    """Orchestrates complete benchmark execution"""

    def __init__(self, results_dir: str = "benchmark_results"):
        self.results_dir = results_dir
        self.env_manager = BenchmarkEnvironment()
        self.data_manager = BenchmarkDataManager()
        self.performance_monitor = PerformanceMonitor()

        # Create results directory
        os.makedirs(results_dir, exist_ok=True)

    def run_competitive_benchmark(self, test_scope: str = "standard") -> BenchmarkReport:
        """Run full competitive benchmark suite"""

        print("🚀 Starting IRIS Graph-AI Competitive Benchmark Suite")
        print("=" * 70)

        # Get benchmark configuration
        config = get_benchmark_config(test_scope)

        # Initialize report
        report = BenchmarkReport(
            benchmark_config=config,
            start_time=datetime.now()
        )

        print(f"Benchmark Scope: {test_scope}")
        print(f"Systems: {[s.value for s in config['systems']]}")
        print(f"Datasets: {config['datasets']}")
        print(f"Test Categories: {[c.value for c in config['test_categories']]}")
        print(f"Concurrent Users: {config['concurrent_users']}")
        print(f"Iterations: {config['iterations']}")
        print()

        try:
            # Run benchmarks for each system
            for system in config['systems']:
                print(f"\n🔧 Benchmarking {system.value}")
                print("-" * 50)

                system_results = []

                for dataset_name in config['datasets']:
                    print(f"\nDataset: {dataset_name}")

                    try:
                        result = self._run_system_benchmark(
                            system, dataset_name, config
                        )
                        system_results.append(result)

                        print(f"✅ {system.value} + {dataset_name} completed")

                    except Exception as e:
                        error_msg = f"Failed {system.value} + {dataset_name}: {e}"
                        print(f"❌ {error_msg}")

                        # Create error result
                        error_result = BenchmarkResult(
                            system=system,
                            dataset_name=dataset_name,
                            performance_metrics=PerformanceMetrics(system=system),
                            errors=[error_msg]
                        )
                        system_results.append(error_result)

                report.system_results[system.value] = system_results

            # Generate analysis
            report.end_time = datetime.now()
            report.total_duration_hours = (
                report.end_time - report.start_time
            ).total_seconds() / 3600

            self._generate_summary_analysis(report)
            self._generate_competitive_analysis(report)

            # Export results
            self._export_benchmark_report(report, test_scope)

            print("\n🎉 Competitive Benchmark Completed!")
            print(f"Total Duration: {report.total_duration_hours:.2f} hours")
            print(f"Results saved to: {self.results_dir}")

            return report

        except Exception as e:
            print(f"\n❌ Benchmark suite failed: {e}")
            report.end_time = datetime.now()
            raise

    def _run_system_benchmark(self, system: DatabaseSystem, dataset_name: str,
                            config: Dict[str, Any]) -> BenchmarkResult:
        """Run benchmark for a single system and dataset"""

        result = BenchmarkResult(
            system=system,
            dataset_name=dataset_name,
            performance_metrics=PerformanceMetrics(system=system)
        )

        benchmark_start = time.time()

        try:
            # 1. Setup environment
            print(f"  Setting up {system.value} environment...")
            setup_start = time.time()

            env_status = self._setup_system_environment(system)
            if env_status.status != 'running':
                raise Exception(f"Environment setup failed: {env_status.error_message}")

            result.environment_setup_time = time.time() - setup_start
            print(f"    Environment ready in {result.environment_setup_time:.1f}s")

            # 2. Load dataset
            print(f"  Loading dataset: {dataset_name}...")
            loading_start = time.time()

            dataset = self._load_benchmark_dataset(dataset_name, system)

            result.data_loading_time = time.time() - loading_start
            print(f"    Dataset loaded in {result.data_loading_time:.1f}s")

            # 3. Generate queries
            print(f"  Generating test queries...")
            query_generator = QueryGenerator(dataset)
            query_sets = self._filter_query_sets(
                query_generator.get_all_query_sets(),
                config['test_categories']
            )

            print(f"    Generated {sum(len(qs.queries) for qs in query_sets)} queries across {len(query_sets)} test categories")

            # 4. Execute benchmark tests
            print(f"  Executing benchmark tests...")

            connection_config = self._get_connection_config(system, env_status)

            # Run tests for each user concurrency level
            for user_count in config['concurrent_users']:
                print(f"    Testing with {user_count} concurrent users...")

                for query_set in query_sets:
                    set_name = f"{query_set.name}_{user_count}users"

                    # Run multiple iterations for statistical significance
                    iteration_metrics = []

                    for iteration in range(config['iterations']):
                        try:
                            metrics = self.performance_monitor.measure_concurrent_performance(
                                query_set.queries[:10],  # Limit queries for performance
                                user_count,
                                system,
                                connection_config
                            )
                            iteration_metrics.append(metrics)

                        except Exception as e:
                            print(f"      Warning: Iteration {iteration} failed: {e}")

                    # Aggregate iteration results
                    if iteration_metrics:
                        aggregated_metrics = self._aggregate_metrics(iteration_metrics)
                        result.query_set_results[set_name] = aggregated_metrics

                        print(f"      {query_set.category.value}: {aggregated_metrics.avg_latency_ms:.2f}ms avg, {aggregated_metrics.queries_per_second:.1f} QPS")

            # Calculate overall performance metrics
            result.performance_metrics = self._calculate_overall_metrics(result.query_set_results)

        except Exception as e:
            error_msg = f"System benchmark failed: {e}"
            result.errors.append(error_msg)
            print(f"    ❌ {error_msg}")

        finally:
            # Cleanup environment
            try:
                self.env_manager.teardown_environment(system.value.lower())
            except Exception as e:
                print(f"    Warning: Cleanup failed: {e}")

        result.total_benchmark_time = time.time() - benchmark_start
        return result

    def _setup_system_environment(self, system: DatabaseSystem) -> EnvironmentStatus:
        """Setup environment for specific system"""

        system_configs = BenchmarkRequirements.SYSTEM_CONFIGS
        config = system_configs.get(system)

        if not config:
            raise ValueError(f"No configuration found for {system}")

        if system == DatabaseSystem.IRIS_GRAPH_AI:
            return self.env_manager.setup_iris_environment(config)
        elif system == DatabaseSystem.NEO4J_ENTERPRISE:
            return self.env_manager.setup_neo4j_environment(config)
        elif system == DatabaseSystem.NEO4J_COMMUNITY:
            # Use community image
            community_config = config
            community_config.docker_image = "neo4j:5-community"
            return self.env_manager.setup_neo4j_environment(community_config)
        elif system == DatabaseSystem.ARANGODB:
            return self.env_manager.setup_arangodb_environment(config)
        else:
            raise ValueError(f"Unsupported system: {system}")

    def _load_benchmark_dataset(self, dataset_name: str, system: DatabaseSystem) -> Dataset:
        """Load or generate benchmark dataset"""

        dataset_specs = BenchmarkRequirements.DATASET_SPECS
        spec = dataset_specs.get(dataset_name)

        if not spec:
            raise ValueError(f"Unknown dataset: {dataset_name}")

        # Check if dataset already exists
        existing_dataset = self.data_manager.get_dataset(dataset_name)
        if existing_dataset:
            print(f"    Using cached dataset: {dataset_name}")
            dataset = existing_dataset
        else:
            # Generate or load dataset
            if spec.data_source == "synthetic_generator":
                dataset = self.data_manager.generate_synthetic_graph(spec)
            else:
                dataset = self.data_manager.load_real_dataset(spec)

        # Load dataset into the target system
        if system == DatabaseSystem.IRIS_GRAPH_AI:
            self.data_manager.load_data_to_iris(dataset)
        elif system in [DatabaseSystem.NEO4J_ENTERPRISE, DatabaseSystem.NEO4J_COMMUNITY]:
            self.data_manager.load_data_to_neo4j(dataset, "bolt://localhost:7687")
        elif system == DatabaseSystem.ARANGODB:
            self.data_manager.load_data_to_arangodb(dataset, "http://localhost:8529")

        return dataset

    def _filter_query_sets(self, query_sets: List[QuerySet],
                          test_categories: List[TestCategory]) -> List[QuerySet]:
        """Filter query sets by test categories"""
        return [qs for qs in query_sets if qs.category in test_categories]

    def _get_connection_config(self, system: DatabaseSystem,
                             env_status: EnvironmentStatus) -> Dict[str, Any]:
        """Get connection configuration for system"""

        if system == DatabaseSystem.IRIS_GRAPH_AI:
            return {
                'host': 'localhost',
                'port': 1973,
                'namespace': 'USER',
                'username': '_SYSTEM',
                'password': 'SYS'
            }
        elif system in [DatabaseSystem.NEO4J_ENTERPRISE, DatabaseSystem.NEO4J_COMMUNITY]:
            return {
                'uri': 'bolt://localhost:7687',
                'username': 'neo4j',
                'password': 'benchmarkpassword'
            }
        elif system == DatabaseSystem.ARANGODB:
            return {
                'url': 'http://localhost:8529',
                'username': 'root',
                'password': 'benchmarkpassword'
            }
        else:
            return {}

    def _aggregate_metrics(self, metrics_list: List[PerformanceMetrics]) -> PerformanceMetrics:
        """Aggregate multiple performance metrics"""

        if not metrics_list:
            return PerformanceMetrics(system=DatabaseSystem.IRIS_GRAPH_AI)

        # Use first metrics as base
        aggregated = metrics_list[0]

        if len(metrics_list) > 1:
            # Average the metrics
            latencies = [m.avg_latency_ms for m in metrics_list]
            aggregated.avg_latency_ms = sum(latencies) / len(latencies)

            throughputs = [m.queries_per_second for m in metrics_list]
            aggregated.queries_per_second = sum(throughputs) / len(throughputs)

            # Use max for peaks
            peak_memories = [m.peak_memory_mb for m in metrics_list]
            aggregated.peak_memory_mb = max(peak_memories)

            peak_cpus = [m.peak_cpu_percent for m in metrics_list]
            aggregated.peak_cpu_percent = max(peak_cpus)

        return aggregated

    def _calculate_overall_metrics(self, query_set_results: Dict[str, PerformanceMetrics]) -> PerformanceMetrics:
        """Calculate overall performance metrics from query set results"""

        if not query_set_results:
            return PerformanceMetrics(system=DatabaseSystem.IRIS_GRAPH_AI)

        # Aggregate all metrics
        all_metrics = list(query_set_results.values())
        return self._aggregate_metrics(all_metrics)

    def _generate_summary_analysis(self, report: BenchmarkReport):
        """Generate summary analysis of benchmark results"""

        summary = {
            'total_systems_tested': len(report.system_results),
            'successful_benchmarks': 0,
            'failed_benchmarks': 0,
            'performance_by_system': {},
            'performance_by_category': {}
        }

        for system_name, results in report.system_results.items():
            system_summary = {
                'datasets_tested': len(results),
                'avg_latency_ms': 0,
                'avg_throughput_qps': 0,
                'error_count': 0
            }

            successful_results = [r for r in results if not r.errors]
            summary['successful_benchmarks'] += len(successful_results)
            summary['failed_benchmarks'] += len(results) - len(successful_results)

            if successful_results:
                latencies = [r.performance_metrics.avg_latency_ms for r in successful_results]
                throughputs = [r.performance_metrics.queries_per_second for r in successful_results]

                system_summary['avg_latency_ms'] = sum(latencies) / len(latencies)
                system_summary['avg_throughput_qps'] = sum(throughputs) / len(throughputs)

            system_summary['error_count'] = sum(len(r.errors) for r in results)
            summary['performance_by_system'][system_name] = system_summary

        report.summary = summary

    def _generate_competitive_analysis(self, report: BenchmarkReport):
        """Generate competitive analysis comparing systems"""

        analysis = {
            'iris_vs_competitors': {},
            'performance_gaps': {},
            'recommendations': []
        }

        # Find IRIS results
        iris_results = report.system_results.get('iris_graph_ai', [])

        if iris_results:
            iris_performance = iris_results[0].performance_metrics if iris_results else None

            # Compare with each competitor
            for system_name, results in report.system_results.items():
                if system_name == 'iris_graph_ai' or not results:
                    continue

                competitor_performance = results[0].performance_metrics

                if iris_performance and competitor_performance:
                    # Calculate performance ratios
                    latency_ratio = (
                        iris_performance.avg_latency_ms / competitor_performance.avg_latency_ms
                        if competitor_performance.avg_latency_ms > 0 else float('inf')
                    )

                    throughput_ratio = (
                        iris_performance.queries_per_second / competitor_performance.queries_per_second
                        if competitor_performance.queries_per_second > 0 else 0
                    )

                    analysis['iris_vs_competitors'][system_name] = {
                        'latency_ratio': latency_ratio,  # Lower is better for IRIS
                        'throughput_ratio': throughput_ratio,  # Higher is better for IRIS
                        'iris_faster': latency_ratio < 1.0,
                        'iris_higher_throughput': throughput_ratio > 1.0
                    }

        # Generate recommendations based on results
        if analysis['iris_vs_competitors']:
            faster_count = sum(1 for comp in analysis['iris_vs_competitors'].values() if comp['iris_faster'])
            total_comparisons = len(analysis['iris_vs_competitors'])

            if faster_count == total_comparisons:
                analysis['recommendations'].append("IRIS shows competitive advantage across all tested systems")
            elif faster_count > total_comparisons / 2:
                analysis['recommendations'].append("IRIS shows competitive advantage in majority of comparisons")
            else:
                analysis['recommendations'].append("IRIS needs optimization to compete effectively")

        report.competitive_analysis = analysis

    def _export_benchmark_report(self, report: BenchmarkReport, test_scope: str):
        """Export benchmark report to files"""

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Export full report as JSON
        report_file = os.path.join(
            self.results_dir,
            f"benchmark_report_{test_scope}_{timestamp}.json"
        )

        # Convert report to serializable format
        report_data = {
            'benchmark_config': report.benchmark_config,
            'start_time': report.start_time.isoformat(),
            'end_time': report.end_time.isoformat() if report.end_time else None,
            'total_duration_hours': report.total_duration_hours,
            'summary': report.summary,
            'competitive_analysis': report.competitive_analysis,
            'system_results': {}
        }

        # Convert system results
        for system_name, results in report.system_results.items():
            report_data['system_results'][system_name] = []

            for result in results:
                result_data = {
                    'system': result.system.value,
                    'dataset_name': result.dataset_name,
                    'environment_setup_time': result.environment_setup_time,
                    'data_loading_time': result.data_loading_time,
                    'total_benchmark_time': result.total_benchmark_time,
                    'errors': result.errors,
                    'performance_metrics': {
                        'avg_latency_ms': result.performance_metrics.avg_latency_ms,
                        'p95_latency_ms': result.performance_metrics.p95_latency_ms,
                        'queries_per_second': result.performance_metrics.queries_per_second,
                        'peak_memory_mb': result.performance_metrics.peak_memory_mb,
                        'peak_cpu_percent': result.performance_metrics.peak_cpu_percent
                    }
                }
                report_data['system_results'][system_name].append(result_data)

        with open(report_file, 'w') as f:
            json.dump(report_data, f, indent=2)

        print(f"✅ Full report exported to: {report_file}")

        # Export executive summary
        summary_file = os.path.join(
            self.results_dir,
            f"executive_summary_{test_scope}_{timestamp}.md"
        )

        self._export_executive_summary(report, summary_file)

    def _export_executive_summary(self, report: BenchmarkReport, filename: str):
        """Export executive summary in markdown format"""

        with open(filename, 'w') as f:
            f.write(f"# IRIS Graph-AI Competitive Benchmark Report\n\n")
            f.write(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"**Duration:** {report.total_duration_hours:.2f} hours\n\n")

            # Executive Summary
            f.write("## Executive Summary\n\n")
            summary = report.summary
            f.write(f"- **Systems Tested:** {summary['total_systems_tested']}\n")
            f.write(f"- **Successful Benchmarks:** {summary['successful_benchmarks']}\n")
            f.write(f"- **Failed Benchmarks:** {summary['failed_benchmarks']}\n\n")

            # Performance by System
            f.write("## Performance by System\n\n")
            f.write("| System | Avg Latency (ms) | Avg Throughput (QPS) | Errors |\n")
            f.write("|--------|------------------|---------------------|--------|\n")

            for system, perf in summary['performance_by_system'].items():
                f.write(f"| {system} | {perf['avg_latency_ms']:.2f} | {perf['avg_throughput_qps']:.1f} | {perf['error_count']} |\n")

            # Competitive Analysis
            f.write("\n## Competitive Analysis\n\n")
            analysis = report.competitive_analysis

            if analysis.get('iris_vs_competitors'):
                f.write("### IRIS vs Competitors\n\n")
                for competitor, comparison in analysis['iris_vs_competitors'].items():
                    f.write(f"**{competitor}:**\n")
                    f.write(f"- Latency: {'✅ IRIS Faster' if comparison['iris_faster'] else '❌ Competitor Faster'} (ratio: {comparison['latency_ratio']:.2f})\n")
                    f.write(f"- Throughput: {'✅ IRIS Higher' if comparison['iris_higher_throughput'] else '❌ Competitor Higher'} (ratio: {comparison['throughput_ratio']:.2f})\n\n")

            # Recommendations
            if analysis.get('recommendations'):
                f.write("### Recommendations\n\n")
                for rec in analysis['recommendations']:
                    f.write(f"- {rec}\n")

        print(f"✅ Executive summary exported to: {filename}")


if __name__ == "__main__":
    # Test benchmark runner
    runner = BenchmarkRunner()

    try:
        # Run quick benchmark
        report = runner.run_competitive_benchmark("quick")

        print("\n" + "=" * 70)
        print("BENCHMARK COMPLETED SUCCESSFULLY")
        print("=" * 70)
        print(f"Duration: {report.total_duration_hours:.2f} hours")
        print(f"Systems tested: {len(report.system_results)}")
        print(f"Results directory: {runner.results_dir}")

    except Exception as e:
        print(f"\n❌ Benchmark failed: {e}")
        import traceback
        traceback.print_exc()