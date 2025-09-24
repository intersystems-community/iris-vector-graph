#!/usr/bin/env python3
"""
Comprehensive test runner for IRIS Graph-AI
Runs all test suites and generates summary report
"""

import sys
import time
import subprocess
import argparse
from pathlib import Path
from typing import Dict, List, Tuple
import json

# Test configuration
TEST_MODULES = [
    {
        'name': 'REST API Tests',
        'module': 'test_iris_rest_api.py',
        'description': 'Tests IRIS REST API endpoints',
        'category': 'api'
    },
    {
        'name': 'Python SDK Tests',
        'module': 'test_python_sdk.py',
        'description': 'Tests direct IRIS Python SDK connectivity',
        'category': 'sdk'
    },
    {
        'name': 'NetworkX Integration Tests',
        'module': 'test_networkx_loader.py',
        'description': 'Tests NetworkX CLI loader functionality',
        'category': 'integration'
    },
    {
        'name': 'Performance Benchmarks',
        'module': 'test_performance_benchmarks.py',
        'description': 'Performance and scalability benchmarks',
        'category': 'performance'
    }
]

class TestRunner:
    """Orchestrates execution of all test suites"""

    def __init__(self, test_dir: Path):
        self.test_dir = test_dir
        self.results = {}
        self.start_time = None
        self.end_time = None

    def run_test_module(self, test_module: Dict) -> Dict:
        """Run a single test module and capture results"""
        module_path = self.test_dir / test_module['module']

        if not module_path.exists():
            return {
                'status': 'skipped',
                'reason': 'Module file not found',
                'duration': 0,
                'output': '',
                'error': f"File not found: {module_path}"
            }

        print(f"\n{'='*60}")
        print(f"Running {test_module['name']}")
        print(f"{'='*60}")

        start_time = time.time()

        try:
            # Run the test module
            result = subprocess.run(
                [sys.executable, str(module_path)],
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )

            duration = time.time() - start_time

            if result.returncode == 0:
                status = 'passed'
                print(f"✅ {test_module['name']} completed successfully")
            else:
                status = 'failed'
                print(f"❌ {test_module['name']} failed")

            return {
                'status': status,
                'duration': duration,
                'output': result.stdout,
                'error': result.stderr,
                'return_code': result.returncode
            }

        except subprocess.TimeoutExpired:
            duration = time.time() - start_time
            print(f"⏰ {test_module['name']} timed out after {duration:.1f}s")

            return {
                'status': 'timeout',
                'duration': duration,
                'output': '',
                'error': 'Test execution timed out',
                'return_code': -1
            }

        except Exception as e:
            duration = time.time() - start_time
            print(f"💥 {test_module['name']} crashed: {e}")

            return {
                'status': 'error',
                'duration': duration,
                'output': '',
                'error': str(e),
                'return_code': -1
            }

    def run_all_tests(self, categories: List[str] = None) -> Dict:
        """Run all test modules or specific categories"""
        self.start_time = time.time()

        print("🚀 Starting IRIS Graph-AI Test Suite")
        print(f"Test directory: {self.test_dir}")

        # Filter tests by category if specified
        tests_to_run = TEST_MODULES
        if categories:
            tests_to_run = [t for t in TEST_MODULES if t['category'] in categories]

        print(f"Running {len(tests_to_run)} test modules...")

        # Run each test module
        for test_module in tests_to_run:
            result = self.run_test_module(test_module)
            self.results[test_module['name']] = {
                **test_module,
                **result
            }

        self.end_time = time.time()

        return self.generate_summary()

    def generate_summary(self) -> Dict:
        """Generate test execution summary"""
        total_duration = self.end_time - self.start_time

        # Count results by status
        status_counts = {}
        for result in self.results.values():
            status = result['status']
            status_counts[status] = status_counts.get(status, 0) + 1

        # Calculate totals
        total_tests = len(self.results)
        passed_tests = status_counts.get('passed', 0)
        failed_tests = status_counts.get('failed', 0)
        skipped_tests = status_counts.get('skipped', 0)
        timeout_tests = status_counts.get('timeout', 0)
        error_tests = status_counts.get('error', 0)

        success_rate = (passed_tests / total_tests * 100) if total_tests > 0 else 0

        summary = {
            'total_duration': total_duration,
            'total_tests': total_tests,
            'passed': passed_tests,
            'failed': failed_tests,
            'skipped': skipped_tests,
            'timeout': timeout_tests,
            'error': error_tests,
            'success_rate': success_rate,
            'results': self.results
        }

        return summary

    def print_summary(self, summary: Dict):
        """Print formatted test summary"""
        print(f"\n{'='*80}")
        print("🏁 IRIS Graph-AI Test Suite Summary")
        print(f"{'='*80}")

        print(f"Total Duration: {summary['total_duration']:.1f} seconds")
        print(f"Total Tests: {summary['total_tests']}")
        print(f"Success Rate: {summary['success_rate']:.1f}%")
        print()

        # Status breakdown
        print("Results by Status:")
        print(f"  ✅ Passed:  {summary['passed']}")
        print(f"  ❌ Failed:  {summary['failed']}")
        print(f"  ⏭️  Skipped: {summary['skipped']}")
        print(f"  ⏰ Timeout: {summary['timeout']}")
        print(f"  💥 Error:   {summary['error']}")
        print()

        # Individual test results
        print("Individual Test Results:")
        for name, result in summary['results'].items():
            status_icon = {
                'passed': '✅',
                'failed': '❌',
                'skipped': '⏭️',
                'timeout': '⏰',
                'error': '💥'
            }.get(result['status'], '❓')

            print(f"  {status_icon} {name:<30} "
                  f"{result['status']:<8} "
                  f"{result['duration']:6.1f}s")

        # Failed test details
        failed_results = {name: result for name, result in summary['results'].items()
                         if result['status'] in ['failed', 'timeout', 'error']}

        if failed_results:
            print(f"\n{'='*80}")
            print("❌ Failed Test Details")
            print(f"{'='*80}")

            for name, result in failed_results.items():
                print(f"\n{name}:")
                print(f"  Status: {result['status']}")
                print(f"  Duration: {result['duration']:.1f}s")

                if result['error']:
                    print(f"  Error: {result['error'][:500]}...")

                if result['output'] and result['status'] == 'failed':
                    print(f"  Output: {result['output'][-500:]}")

    def save_results(self, output_file: Path, summary: Dict):
        """Save detailed results to JSON file"""
        with open(output_file, 'w') as f:
            json.dump({
                'timestamp': time.time(),
                'summary': summary
            }, f, indent=2, default=str)

        print(f"\n📄 Detailed results saved to: {output_file}")


def check_prerequisites():
    """Check if required dependencies are available"""
    missing_deps = []

    try:
        import iris
    except ImportError:
        missing_deps.append("intersystems_irispython")

    try:
        import networkx
    except ImportError:
        missing_deps.append("networkx")

    try:
        import requests
    except ImportError:
        missing_deps.append("requests")

    try:
        import pandas
    except ImportError:
        missing_deps.append("pandas")

    try:
        import numpy
    except ImportError:
        missing_deps.append("numpy")

    if missing_deps:
        print("❌ Missing required dependencies:")
        for dep in missing_deps:
            print(f"  - {dep}")
        print("\nInstall missing dependencies with:")
        print(f"  pip install {' '.join(missing_deps)}")
        return False

    print("✅ All required dependencies are available")
    return True


def main():
    """Main test runner entry point"""
    parser = argparse.ArgumentParser(
        description="Run IRIS Graph-AI test suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Test Categories:
  api          - REST API tests
  sdk          - Python SDK tests
  integration  - NetworkX integration tests
  performance  - Performance benchmarks

Examples:
  python run_all_tests.py                    # Run all tests
  python run_all_tests.py --category api     # Run only API tests
  python run_all_tests.py --category performance --output results.json
        """
    )

    parser.add_argument(
        '--category',
        action='append',
        choices=['api', 'sdk', 'integration', 'performance'],
        help='Run specific test categories (can be specified multiple times)'
    )

    parser.add_argument(
        '--output',
        type=Path,
        default='test_results.json',
        help='Output file for detailed results (default: test_results.json)'
    )

    parser.add_argument(
        '--no-prereq-check',
        action='store_true',
        help='Skip prerequisite dependency check'
    )

    parser.add_argument(
        '--quick',
        action='store_true',
        help='Run quick tests only (skip performance benchmarks)'
    )

    args = parser.parse_args()

    # Quick mode excludes performance tests
    if args.quick and not args.category:
        args.category = ['api', 'sdk', 'integration']

    # Check prerequisites
    if not args.no_prereq_check:
        if not check_prerequisites():
            return 1

    # Initialize test runner
    test_dir = Path(__file__).parent
    runner = TestRunner(test_dir)

    try:
        # Run tests
        summary = runner.run_all_tests(categories=args.category)

        # Print summary
        runner.print_summary(summary)

        # Save detailed results
        runner.save_results(args.output, summary)

        # Return appropriate exit code
        if summary['failed'] > 0 or summary['error'] > 0:
            print(f"\n❌ Test suite failed ({summary['failed']} failed, {summary['error']} errors)")
            return 1
        elif summary['timeout'] > 0:
            print(f"\n⏰ Test suite had timeouts ({summary['timeout']} timed out)")
            return 2
        elif summary['passed'] == 0:
            print("\n⚠️  No tests passed")
            return 3
        else:
            print(f"\n✅ All tests passed! ({summary['passed']}/{summary['total_tests']})")
            return 0

    except KeyboardInterrupt:
        print("\n🛑 Test execution interrupted by user")
        return 130

    except Exception as e:
        print(f"\n💥 Test runner crashed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())