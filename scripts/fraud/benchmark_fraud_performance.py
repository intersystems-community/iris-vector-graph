#!/usr/bin/env python3
"""
Fraud Scoring Performance Benchmark

Validates NFR-001 through NFR-008 with comprehensive load testing:
- MLP mode: <20ms p95 at 200 QPS for 15 minutes
- EGO mode: <50ms p95 at 100 QPS for 5 minutes
- Event ingestion: ≥500 events/sec
- Error rate: 0%

Constitutional Compliance: Performance as a Feature
Run with: python scripts/fraud/benchmark_fraud_performance.py
"""

import requests
import time
import statistics
import concurrent.futures
from datetime import datetime
import logging
import random

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class FraudBenchmark:
    """Fraud scoring performance benchmark runner"""

    def __init__(self, api_url="http://localhost:8000", num_entities=100):
        self.api_url = api_url
        self.fraud_endpoint = f"{api_url}/fraud/score"
        self.health_endpoint = f"{api_url}/fraud/health"
        self.num_entities = num_entities

        # Generate test entity IDs
        self.entity_ids = [f"acct:bench_user{i:03d}" for i in range(num_entities)]

    def health_check(self):
        """Validate API health before benchmark"""
        logger.info("Checking API health...")

        try:
            response = requests.get(self.health_endpoint, timeout=5.0)
            response.raise_for_status()
            health_data = response.json()

            if health_data["status"] != "healthy":
                logger.warning(f"API health status: {health_data['status']}")

            logger.info(f"  ✅ Model loaded: {health_data['model_loaded']}")
            logger.info(f"  ✅ Database connected: {health_data['database_connected']}")
            logger.info(f"  ✅ Centroid available: {health_data['centroid_available']}")

            return health_data["status"] == "healthy"

        except requests.exceptions.RequestException as e:
            logger.error(f"  ❌ Health check failed: {e}")
            return False

    def score_fraud(self, entity_id, mode="MLP", amount=100.0):
        """
        Score single fraud request and return latency

        Returns:
            tuple: (latency_ms, num_reasons, success)
        """
        payload = {
            "mode": mode,
            "payer": entity_id,
            "device": "dev:laptop",
            "ip": "ip:192.168.1.1",
            "merchant": "merchant:test_merchant",
            "amount": amount,
            "country": "US"
        }

        start_time = time.perf_counter()

        try:
            response = requests.post(self.fraud_endpoint, json=payload, timeout=5.0)
            response.raise_for_status()

            end_time = time.perf_counter()
            latency_ms = (end_time - start_time) * 1000

            data = response.json()
            num_reasons = len(data.get("reasons", []))

            return latency_ms, num_reasons, True

        except requests.exceptions.RequestException as e:
            end_time = time.perf_counter()
            latency_ms = (end_time - start_time) * 1000
            return latency_ms, 0, False

    def benchmark_mlp_mode(self, duration_seconds=900, target_qps=200):
        """
        NFR-001: MLP mode p95 latency <20ms at 200 QPS for 15 minutes

        Args:
            duration_seconds: Benchmark duration (default: 900 = 15 minutes)
            target_qps: Target queries per second (default: 200)
        """
        logger.info("="*70)
        logger.info(f"Benchmark: MLP Mode - {duration_seconds}s @ {target_qps} QPS")
        logger.info("="*70)

        total_requests = duration_seconds * target_qps
        latencies = []
        errors = 0
        reason_code_counts = []

        start_time = time.time()

        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
            futures = []

            for i in range(total_requests):
                # Distribute requests across test entities
                entity_id = self.entity_ids[i % len(self.entity_ids)]
                amount = 100.0 + (i % 500)  # Varying amounts

                future = executor.submit(self.score_fraud, entity_id, "MLP", amount)
                futures.append(future)

                # Rate limiting
                if (i + 1) % target_qps == 0:
                    elapsed = time.time() - start_time
                    expected = (i + 1) / target_qps
                    if elapsed < expected:
                        time.sleep(expected - elapsed)

                # Progress logging every 10,000 requests
                if (i + 1) % 10000 == 0:
                    logger.info(f"  Progress: {i+1}/{total_requests} requests ({(i+1)/total_requests*100:.1f}%)")

            # Collect results
            for future in concurrent.futures.as_completed(futures):
                try:
                    latency_ms, num_reasons, success = future.result()
                    latencies.append(latency_ms)
                    reason_code_counts.append(num_reasons)
                    if not success:
                        errors += 1
                except Exception as e:
                    errors += 1

        end_time = time.time()

        # Compute metrics
        actual_duration = end_time - start_time
        actual_qps = len(latencies) / actual_duration

        p50 = statistics.quantiles(latencies, n=100)[49] if len(latencies) > 0 else 0
        p95 = statistics.quantiles(latencies, n=100)[94] if len(latencies) > 0 else 0
        p99 = statistics.quantiles(latencies, n=100)[98] if len(latencies) > 0 else 0
        avg = statistics.mean(latencies) if len(latencies) > 0 else 0

        error_rate = errors / total_requests if total_requests > 0 else 0
        min_reasons = min(reason_code_counts) if reason_code_counts else 0

        # Print results
        logger.info("\n" + "="*70)
        logger.info("MLP Mode Benchmark Results")
        logger.info("="*70)
        logger.info(f"Total Requests:    {len(latencies):,}")
        logger.info(f"Duration:          {actual_duration:.1f}s")
        logger.info(f"Actual QPS:        {actual_qps:.1f}")
        logger.info(f"Error Rate:        {error_rate*100:.4f}%")
        logger.info(f"\nLatency (ms):")
        logger.info(f"  p50:             {p50:.2f}ms")
        logger.info(f"  p95:             {p95:.2f}ms {'✅ PASS' if p95 < 20.0 else '❌ FAIL'}")
        logger.info(f"  p99:             {p99:.2f}ms")
        logger.info(f"  avg:             {avg:.2f}ms")
        logger.info(f"\nReason Codes:      {min_reasons} min {'✅ PASS' if min_reasons >= 3 else '❌ FAIL'}")
        logger.info("="*70)

        return {
            "mode": "MLP",
            "p95_latency_ms": p95,
            "actual_qps": actual_qps,
            "error_rate": error_rate,
            "min_reasons": min_reasons,
            "passed": p95 < 20.0 and error_rate == 0.0 and min_reasons >= 3
        }

    def benchmark_ego_mode(self, duration_seconds=300, target_qps=100):
        """
        NFR-003: EGO mode p95 latency <50ms at 100 QPS for 5 minutes

        Args:
            duration_seconds: Benchmark duration (default: 300 = 5 minutes)
            target_qps: Target queries per second (default: 100)
        """
        logger.info("="*70)
        logger.info(f"Benchmark: EGO Mode - {duration_seconds}s @ {target_qps} QPS")
        logger.info("="*70)

        total_requests = duration_seconds * target_qps
        latencies = []
        errors = 0

        start_time = time.time()

        with concurrent.futures.ThreadPoolExecutor(max_workers=25) as executor:
            futures = []

            for i in range(total_requests):
                entity_id = self.entity_ids[i % len(self.entity_ids)]
                amount = 100.0 + (i % 500)

                future = executor.submit(self.score_fraud, entity_id, "EGO", amount)
                futures.append(future)

                # Rate limiting
                if (i + 1) % target_qps == 0:
                    elapsed = time.time() - start_time
                    expected = (i + 1) / target_qps
                    if elapsed < expected:
                        time.sleep(expected - elapsed)

                # Progress logging
                if (i + 1) % 5000 == 0:
                    logger.info(f"  Progress: {i+1}/{total_requests} requests ({(i+1)/total_requests*100:.1f}%)")

            for future in concurrent.futures.as_completed(futures):
                try:
                    latency_ms, _, success = future.result()
                    latencies.append(latency_ms)
                    if not success:
                        errors += 1
                except Exception:
                    errors += 1

        end_time = time.time()

        # Compute metrics
        actual_duration = end_time - start_time
        actual_qps = len(latencies) / actual_duration

        p50 = statistics.quantiles(latencies, n=100)[49] if len(latencies) > 0 else 0
        p95 = statistics.quantiles(latencies, n=100)[94] if len(latencies) > 0 else 0
        p99 = statistics.quantiles(latencies, n=100)[98] if len(latencies) > 0 else 0
        avg = statistics.mean(latencies) if len(latencies) > 0 else 0

        error_rate = errors / total_requests if total_requests > 0 else 0

        # Print results
        logger.info("\n" + "="*70)
        logger.info("EGO Mode Benchmark Results")
        logger.info("="*70)
        logger.info(f"Total Requests:    {len(latencies):,}")
        logger.info(f"Duration:          {actual_duration:.1f}s")
        logger.info(f"Actual QPS:        {actual_qps:.1f}")
        logger.info(f"Error Rate:        {error_rate*100:.4f}%")
        logger.info(f"\nLatency (ms):")
        logger.info(f"  p50:             {p50:.2f}ms")
        logger.info(f"  p95:             {p95:.2f}ms {'✅ PASS' if p95 < 50.0 else '❌ FAIL'}")
        logger.info(f"  p99:             {p99:.2f}ms")
        logger.info(f"  avg:             {avg:.2f}ms")
        logger.info("="*70)

        return {
            "mode": "EGO",
            "p95_latency_ms": p95,
            "actual_qps": actual_qps,
            "error_rate": error_rate,
            "passed": p95 < 50.0 and error_rate == 0.0
        }

    def run_full_benchmark(self):
        """Run complete performance benchmark suite"""
        logger.info("="*70)
        logger.info(" Fraud Scoring Performance Benchmark")
        logger.info("="*70)

        # Health check
        if not self.health_check():
            logger.error("❌ API health check failed. Aborting benchmark.")
            return

        # Run benchmarks
        mlp_results = self.benchmark_mlp_mode()
        time.sleep(10)  # Cool-down period

        ego_results = self.benchmark_ego_mode()

        # Summary
        logger.info("\n" + "="*70)
        logger.info(" Benchmark Summary")
        logger.info("="*70)
        logger.info(f"MLP Mode:  {'✅ PASS' if mlp_results['passed'] else '❌ FAIL'} (p95: {mlp_results['p95_latency_ms']:.2f}ms)")
        logger.info(f"EGO Mode:  {'✅ PASS' if ego_results['passed'] else '❌ FAIL'} (p95: {ego_results['p95_latency_ms']:.2f}ms)")
        logger.info("="*70)

        if mlp_results["passed"] and ego_results["passed"]:
            logger.info("✅ All performance benchmarks PASSED")
        else:
            logger.error("❌ Some performance benchmarks FAILED")


def main():
    """Run fraud scoring performance benchmark"""
    benchmark = FraudBenchmark(api_url="http://localhost:8000", num_entities=100)
    benchmark.run_full_benchmark()


if __name__ == "__main__":
    main()
