#!/usr/bin/env python3
"""
IRIS Graph-AI Competitive Benchmarking - Main Entry Point

Run competitive benchmarks against Neo4j, Neptune, and other graph databases
to validate IRIS Graph-AI's enterprise readiness and market positioning.

Usage:
    python run_competitive_benchmark.py [--scope SCOPE] [--systems SYSTEMS] [--output OUTPUT]

Examples:
    # Quick validation (recommended first run)
    python run_competitive_benchmark.py --scope quick

    # Standard benchmark suite
    python run_competitive_benchmark.py --scope standard

    # Comprehensive enterprise benchmark
    python run_competitive_benchmark.py --scope comprehensive

    # IRIS vs Neo4j only
    python run_competitive_benchmark.py --systems iris_graph_ai,neo4j_community

    # Custom output directory
    python run_competitive_benchmark.py --output ./custom_results
"""

import sys
import os
import argparse
from pathlib import Path

# Add framework to Python path
framework_path = Path(__file__).parent / "framework"
sys.path.insert(0, str(framework_path))

from benchmark_config import DatabaseSystem, BenchmarkRequirements
from benchmark_runner import BenchmarkRunner


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="IRIS Graph-AI Competitive Benchmarking Suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument(
        '--scope',
        choices=['quick', 'standard', 'comprehensive'],
        default='standard',
        help='Benchmark scope (default: standard)'
    )

    parser.add_argument(
        '--systems',
        type=str,
        help='Comma-separated list of systems to benchmark (e.g., iris_graph_ai,neo4j_community)'
    )

    parser.add_argument(
        '--output',
        type=str,
        default='benchmark_results',
        help='Output directory for results (default: benchmark_results)'
    )

    parser.add_argument(
        '--validate-only',
        action='store_true',
        help='Only validate current IRIS performance against baseline requirements'
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be benchmarked without running tests'
    )

    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose output'
    )

    return parser.parse_args()


def validate_iris_baseline():
    """Validate IRIS performance against baseline requirements"""
    print("🔍 Validating IRIS Performance Against Baseline Requirements")
    print("=" * 70)

    validation = BenchmarkRequirements.validate_current_performance()

    for test, passed in validation.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status} {test}")

    overall = all(validation.values())
    print(f"\nOverall Baseline Validation: {'✅ PASS' if overall else '❌ FAIL'}")

    if overall:
        print("\n🎯 IRIS meets baseline requirements for competitive benchmarking")
        print("✅ Ready to proceed with competitive analysis")
    else:
        print("\n⚠️  IRIS needs optimization before competitive benchmarking")
        print("❌ Consider addressing performance gaps first")

    return overall


def show_benchmark_plan(scope: str, systems: list):
    """Show what would be benchmarked"""
    from benchmark_config import get_benchmark_config

    config = get_benchmark_config(scope)

    print(f"📋 Benchmark Plan - Scope: {scope}")
    print("=" * 70)

    print(f"Systems to benchmark: {len(systems)}")
    for system in systems:
        print(f"  • {system.value}")

    print(f"\nDatasets: {len(config['datasets'])}")
    for dataset in config['datasets']:
        print(f"  • {dataset}")

    print(f"\nTest categories: {len(config['test_categories'])}")
    for category in config['test_categories']:
        print(f"  • {category.value}")

    print(f"\nConcurrent users: {config['concurrent_users']}")
    print(f"Iterations per test: {config['iterations']}")

    # Estimate total tests
    total_tests = (
        len(systems) *
        len(config['datasets']) *
        len(config['test_categories']) *
        len(config['concurrent_users']) *
        config['iterations']
    )

    estimated_hours = total_tests * 0.1 / 60  # Rough estimate
    print(f"\nEstimated tests: {total_tests}")
    print(f"Estimated duration: {estimated_hours:.1f} hours")


def main():
    """Main entry point"""
    args = parse_arguments()

    print("🚀 IRIS Graph-AI Competitive Benchmarking Suite")
    print("=" * 70)
    print(f"Scope: {args.scope}")
    print(f"Output directory: {args.output}")
    print()

    # Validate IRIS baseline first if requested
    if args.validate_only:
        success = validate_iris_baseline()
        sys.exit(0 if success else 1)

    # Parse systems to benchmark
    if args.systems:
        system_names = [s.strip() for s in args.systems.split(',')]
        systems = []
        for name in system_names:
            try:
                systems.append(DatabaseSystem(name))
            except ValueError:
                print(f"❌ Unknown system: {name}")
                print(f"Available systems: {[s.value for s in DatabaseSystem]}")
                sys.exit(1)
    else:
        from benchmark_config import get_benchmark_config
        config = get_benchmark_config(args.scope)
        systems = config['systems']

    # Show plan if dry run
    if args.dry_run:
        show_benchmark_plan(args.scope, systems)
        return

    # Validate IRIS baseline before competitive benchmarking
    print("🔍 Pre-benchmark Validation")
    print("-" * 30)

    baseline_valid = validate_iris_baseline()
    if not baseline_valid:
        print("\n⚠️  Warning: IRIS baseline validation failed")
        print("Proceeding with benchmarking for diagnostic purposes...")
        print()

    # Initialize benchmark runner
    runner = BenchmarkRunner(results_dir=args.output)

    # Override systems if specified
    if args.systems:
        from benchmark_config import get_benchmark_config
        config = get_benchmark_config(args.scope)
        config['systems'] = systems

    try:
        # Run benchmark suite
        report = runner.run_competitive_benchmark(args.scope)

        # Print final summary
        print("\n" + "=" * 70)
        print("🎉 COMPETITIVE BENCHMARK COMPLETED")
        print("=" * 70)

        print(f"Duration: {report.total_duration_hours:.2f} hours")
        print(f"Systems tested: {len(report.system_results)}")
        print(f"Results saved to: {args.output}")

        # Print key findings
        if report.competitive_analysis.get('recommendations'):
            print("\n📊 Key Findings:")
            for rec in report.competitive_analysis['recommendations']:
                print(f"  • {rec}")

        # Print validation status
        if baseline_valid:
            print("\n✅ IRIS passed baseline validation")
        else:
            print("\n⚠️  IRIS needs optimization (failed baseline validation)")

        print(f"\n📁 Detailed results: {args.output}/")
        print("📈 Review the executive summary for business recommendations")

    except KeyboardInterrupt:
        print("\n\n⏹️  Benchmark interrupted by user")
        sys.exit(130)

    except Exception as e:
        print(f"\n❌ Benchmark failed: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()