#!/usr/bin/env python3
"""
Performance Validation Script for IRIS Graph Core Integration

Validates that the iris_vector_graph module provides the expected performance
improvements and functionality when integrated with rag-templates.
"""

import sys
import time
import json
import logging
from pathlib import Path
from typing import Dict, Any, List

# Add project paths
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Add rag-templates path if available
rag_templates_path = project_root.parent / "rag-templates"
if rag_templates_path.exists():
    sys.path.insert(0, str(rag_templates_path))

import iris
from iris_vector_graph.engine import IRISGraphEngine
from iris_vector_graph.fusion import HybridSearchFusion
from iris_vector_graph.vector_utils import VectorOptimizer

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class IntegrationValidator:
    """Validates iris_vector_graph integration and performance"""

    def __init__(self):
        """Initialize validator with IRIS connection"""
        try:
            # Connect to IRIS (using same approach as rag-templates)
            self.conn = iris.connect('localhost', 1972, 'USER', '_SYSTEM', 'SYS')
            self.engine = IRISGraphEngine(self.conn)
            self.fusion = HybridSearchFusion(self.engine)
            self.optimizer = VectorOptimizer(self.conn)
            logger.info("Successfully connected to IRIS and initialized components")
        except Exception as e:
            logger.error(f"Failed to initialize: {e}")
            raise

    def validate_hnsw_performance(self) -> Dict[str, Any]:
        """Validate HNSW vector search performance"""
        logger.info("Validating HNSW vector search performance...")

        # Check HNSW availability
        hnsw_status = self.optimizer.check_hnsw_availability()

        if not hnsw_status['available']:
            return {
                'status': 'failed',
                'reason': hnsw_status['reason'],
                'hnsw_available': False
            }

        # Performance test with sample vector
        test_vector = [0.1] * 768  # Standard 768D embedding
        query_vector = json.dumps(test_vector)

        # Time HNSW search
        start_time = time.perf_counter()
        try:
            results = self.engine.kg_KNN_VEC(query_vector, k=10)
            hnsw_time = (time.perf_counter() - start_time) * 1000  # ms
        except Exception as e:
            return {
                'status': 'failed',
                'reason': f'HNSW search failed: {e}',
                'hnsw_available': True
            }

        # Validate performance target (should be < 100ms for production)
        performance_target = 100  # ms
        performance_met = hnsw_time < performance_target

        return {
            'status': 'passed' if performance_met else 'warning',
            'hnsw_available': True,
            'hnsw_time_ms': hnsw_time,
            'performance_target_ms': performance_target,
            'performance_met': performance_met,
            'vector_count': hnsw_status['record_count'],
            'results_count': len(results)
        }

    def validate_rrf_fusion(self) -> Dict[str, Any]:
        """Validate RRF fusion functionality"""
        logger.info("Validating RRF fusion...")

        test_vector = json.dumps([0.1] * 768)
        test_text = "protein interaction"

        try:
            start_time = time.perf_counter()
            rrf_results = self.engine.kg_RRF_FUSE(
                k=10, k1=20, k2=20, c=60,
                query_vector=test_vector,
                query_text=test_text
            )
            rrf_time = (time.perf_counter() - start_time) * 1000

            # Validate RRF results structure
            if not rrf_results:
                return {
                    'status': 'warning',
                    'reason': 'No RRF results returned',
                    'rrf_time_ms': rrf_time
                }

            # Check result format: (entity_id, rrf_score, vector_score, text_score)
            first_result = rrf_results[0]
            valid_format = (len(first_result) == 4 and
                          isinstance(first_result[0], str) and
                          all(isinstance(score, (int, float)) for score in first_result[1:]))

            return {
                'status': 'passed' if valid_format else 'failed',
                'rrf_time_ms': rrf_time,
                'results_count': len(rrf_results),
                'valid_format': valid_format,
                'sample_result': first_result
            }

        except Exception as e:
            return {
                'status': 'failed',
                'reason': f'RRF fusion failed: {e}'
            }

    def validate_hybrid_search(self) -> Dict[str, Any]:
        """Validate multi-modal hybrid search"""
        logger.info("Validating hybrid search...")

        try:
            start_time = time.perf_counter()
            hybrid_results = self.fusion.multi_modal_search(
                query_vector=json.dumps([0.1] * 768),
                query_text="drug target",
                k=10,
                fusion_method="rrf"
            )
            hybrid_time = (time.perf_counter() - start_time) * 1000

            # Validate hybrid results structure
            if not hybrid_results:
                return {
                    'status': 'warning',
                    'reason': 'No hybrid results returned',
                    'hybrid_time_ms': hybrid_time
                }

            # Check result format
            first_result = hybrid_results[0]
            required_fields = ['entity_id', 'fusion_score', 'rank', 'search_modes']
            valid_format = all(field in first_result for field in required_fields)

            return {
                'status': 'passed' if valid_format else 'failed',
                'hybrid_time_ms': hybrid_time,
                'results_count': len(hybrid_results),
                'valid_format': valid_format,
                'search_modes': [mode['mode'] for mode in first_result.get('search_modes', [])]
            }

        except Exception as e:
            return {
                'status': 'failed',
                'reason': f'Hybrid search failed: {e}'
            }

    def validate_rag_templates_integration(self) -> Dict[str, Any]:
        """Validate integration with rag-templates project"""
        logger.info("Validating rag-templates integration...")

        try:
            # Try to import HybridGraphRAGPipeline
            from iris_rag.pipelines.hybrid_graphrag import HybridGraphRAGPipeline

            # Test pipeline initialization
            start_time = time.perf_counter()
            from iris_rag.core.connection import ConnectionManager
            from iris_rag.config.manager import ConfigurationManager

            connection_manager = ConnectionManager()
            config_manager = ConfigurationManager()

            pipeline = HybridGraphRAGPipeline(
                connection_manager=connection_manager,
                config_manager=config_manager
            )
            init_time = (time.perf_counter() - start_time) * 1000

            # Test pipeline capabilities
            has_iris_engine = pipeline.iris_engine is not None
            has_fusion_engine = pipeline.fusion_engine is not None

            return {
                'status': 'passed' if has_iris_engine else 'warning',
                'rag_templates_found': True,
                'pipeline_init_time_ms': init_time,
                'iris_engine_available': has_iris_engine,
                'fusion_engine_available': has_fusion_engine,
                'pipeline_methods': ['hybrid', 'rrf', 'vector', 'text'] if has_iris_engine else ['kg']
            }

        except ImportError as e:
            return {
                'status': 'failed',
                'rag_templates_found': False,
                'reason': f'Cannot import rag-templates: {e}'
            }
        except Exception as e:
            return {
                'status': 'failed',
                'rag_templates_found': True,
                'reason': f'Pipeline initialization failed: {e}'
            }

    def run_comprehensive_validation(self) -> Dict[str, Any]:
        """Run comprehensive validation of the integration"""
        logger.info("Starting comprehensive integration validation...")

        validation_results = {
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'validation_version': '1.0',
            'tests': {}
        }

        # Run individual validation tests
        tests = [
            ('hnsw_performance', self.validate_hnsw_performance),
            ('rrf_fusion', self.validate_rrf_fusion),
            ('hybrid_search', self.validate_hybrid_search),
            ('rag_templates_integration', self.validate_rag_templates_integration)
        ]

        overall_status = 'passed'
        for test_name, test_func in tests:
            logger.info(f"Running {test_name} validation...")
            try:
                result = test_func()
                validation_results['tests'][test_name] = result

                # Update overall status
                if result['status'] == 'failed':
                    overall_status = 'failed'
                elif result['status'] == 'warning' and overall_status == 'passed':
                    overall_status = 'warning'

            except Exception as e:
                logger.error(f"Test {test_name} crashed: {e}")
                validation_results['tests'][test_name] = {
                    'status': 'failed',
                    'reason': f'Test crashed: {e}'
                }
                overall_status = 'failed'

        validation_results['overall_status'] = overall_status

        # Calculate performance summary
        self._calculate_performance_summary(validation_results)

        return validation_results

    def _calculate_performance_summary(self, results: Dict[str, Any]):
        """Calculate performance summary from validation results"""
        tests = results['tests']

        # Collect timing data
        timing_data = {}
        if 'hnsw_performance' in tests and 'hnsw_time_ms' in tests['hnsw_performance']:
            timing_data['hnsw_vector_search'] = tests['hnsw_performance']['hnsw_time_ms']

        if 'rrf_fusion' in tests and 'rrf_time_ms' in tests['rrf_fusion']:
            timing_data['rrf_fusion'] = tests['rrf_fusion']['rrf_time_ms']

        if 'hybrid_search' in tests and 'hybrid_time_ms' in tests['hybrid_search']:
            timing_data['hybrid_search'] = tests['hybrid_search']['hybrid_time_ms']

        # Performance targets
        targets = {
            'hnsw_vector_search': 100,  # ms
            'rrf_fusion': 200,          # ms
            'hybrid_search': 300        # ms
        }

        # Calculate performance score
        performance_scores = []
        for operation, time_ms in timing_data.items():
            target = targets.get(operation, 1000)
            score = min(100, (target / time_ms) * 100) if time_ms > 0 else 0
            performance_scores.append(score)

        avg_performance_score = sum(performance_scores) / len(performance_scores) if performance_scores else 0

        results['performance_summary'] = {
            'timing_data': timing_data,
            'performance_targets': targets,
            'average_performance_score': avg_performance_score,
            'performance_grade': self._get_performance_grade(avg_performance_score)
        }

    def _get_performance_grade(self, score: float) -> str:
        """Get performance grade based on score"""
        if score >= 90:
            return 'A'
        elif score >= 80:
            return 'B'
        elif score >= 70:
            return 'C'
        elif score >= 60:
            return 'D'
        else:
            return 'F'


def main():
    """Run validation and display results"""
    try:
        validator = IntegrationValidator()
        results = validator.run_comprehensive_validation()

        # Display results
        print("=" * 60)
        print("IRIS Graph Core Integration Validation Report")
        print("=" * 60)
        print(f"Timestamp: {results['timestamp']}")
        print(f"Overall Status: {results['overall_status'].upper()}")

        if 'performance_summary' in results:
            perf = results['performance_summary']
            print(f"Performance Grade: {perf['performance_grade']}")
            print(f"Average Performance Score: {perf['average_performance_score']:.1f}/100")

        print("\nTest Results:")
        print("-" * 40)

        for test_name, test_result in results['tests'].items():
            status = test_result['status']
            print(f"{test_name}: {status.upper()}")

            if status == 'passed':
                # Show performance metrics for passed tests
                if 'hnsw_time_ms' in test_result:
                    print(f"  HNSW Performance: {test_result['hnsw_time_ms']:.1f}ms")
                if 'rrf_time_ms' in test_result:
                    print(f"  RRF Performance: {test_result['rrf_time_ms']:.1f}ms")
                if 'hybrid_time_ms' in test_result:
                    print(f"  Hybrid Performance: {test_result['hybrid_time_ms']:.1f}ms")
                if 'results_count' in test_result:
                    print(f"  Results: {test_result['results_count']} items")

            elif status in ['failed', 'warning']:
                print(f"  Reason: {test_result.get('reason', 'Unknown')}")

        # Performance summary
        if 'performance_summary' in results:
            print(f"\nPerformance Summary:")
            print("-" * 40)
            timing = results['performance_summary']['timing_data']
            targets = results['performance_summary']['performance_targets']

            for operation, time_ms in timing.items():
                target = targets.get(operation, 'N/A')
                status_symbol = "✓" if time_ms <= target else "⚠"
                print(f"{operation}: {time_ms:.1f}ms {status_symbol} (target: {target}ms)")

        # Recommendations
        print(f"\nRecommendations:")
        print("-" * 40)

        if results['overall_status'] == 'passed':
            print("✓ Integration is working correctly")
            print("✓ Performance targets are being met")
            print("✓ Ready for production use")
        elif results['overall_status'] == 'warning':
            print("⚠ Integration is functional but has performance concerns")
            print("⚠ Consider optimizing vector data or HNSW parameters")
        else:
            print("✗ Integration has critical issues")
            print("✗ Check IRIS database setup and iris_vector_graph installation")

        # Save detailed results
        results_file = Path(__file__).parent.parent / 'validation_results.json'
        with open(results_file, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\nDetailed results saved to: {results_file}")

    except Exception as e:
        logger.error(f"Validation failed: {e}")
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()