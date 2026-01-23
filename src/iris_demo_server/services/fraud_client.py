"""Resilient fraud API client with circuit breaker"""
import httpx
import time
import os
import json
from typing import Optional, Dict, Any
from pathlib import Path
from datetime import datetime


class CircuitBreaker:
    """Exponential backoff circuit breaker"""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.state = "closed"  # closed, open, half_open

    def is_open(self) -> bool:
        """Check if circuit is open (failing)"""
        if self.state == "open":
            if self.last_failure_time and \
               (time.time() - self.last_failure_time) > self.recovery_timeout:
                self.state = "half_open"
                return False
            return True
        return False

    def record_success(self) -> None:
        """Record successful call"""
        self.failure_count = 0
        self.state = "closed"

    def record_failure(self) -> None:
        """Record failed call"""
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "open"


class FraudAPIClient:
    """Resilient fraud API client (integrates with fraud server on :8100)"""

    def __init__(self, base_url: str = "http://localhost:8100", demo_mode: bool = False):
        self.base_url = base_url
        self.demo_mode = demo_mode or os.getenv("DEMO_MODE", "false").lower() == "true"
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            http2=True
        )
        self.circuit_breaker = CircuitBreaker()

    async def score_transaction(self, transaction: Dict[str, Any]) -> Dict[str, Any]:
        """Score transaction with circuit breaker fallback"""
        if self.demo_mode or self.circuit_breaker.is_open():
            return self._get_demo_score(transaction)

        try:
            response = await self.client.post(
                f"{self.base_url}/fraud/score",
                json={
                    "mode": "MLP",
                    "payer": transaction.get("payer", "acct:unknown"),
                    "device": transaction.get("device", "dev:unknown"),
                    "ip": transaction.get("ip_address", ""),
                    "merchant": transaction.get("merchant", ""),
                    "amount": transaction.get("amount", 0.0)
                }
            )
            response.raise_for_status()
            self.circuit_breaker.record_success()

            # Parse fraud API response
            result = response.json()
            return self._format_fraud_api_response(result, transaction)

        except (httpx.HTTPError, httpx.TimeoutException, Exception):
            self.circuit_breaker.record_failure()
            return self._get_demo_score(transaction)

    def _format_fraud_api_response(self, api_result: Dict[str, Any],
                                   transaction: Dict[str, Any]) -> Dict[str, Any]:
        """Format fraud API response to match our schema"""
        # Extract fraud score from API response
        fraud_prob = api_result.get("fraud_probability",
                                    api_result.get("score", 0.15))

        return {
            "fraud_probability": fraud_prob,
            "risk_classification": self._classify_risk(fraud_prob),
            "contributing_factors": api_result.get("factors",
                                                   ["API scoring result"]),
            "scoring_timestamp": datetime.utcnow().isoformat(),
            "scoring_model": api_result.get("model", "MLP"),
            "confidence": api_result.get("confidence", 0.85)
        }

    def _get_demo_score(self, transaction: Dict[str, Any]) -> Dict[str, Any]:
        """Fallback to demo data (heuristic scoring)"""
        amount = transaction.get("amount", 0)

        # Simple heuristic for demo
        if amount > 10000:
            prob = 0.85
            factors = ["Very high amount", "Demo mode heuristic"]
        elif amount > 5000:
            prob = 0.65
            factors = ["High amount", "Demo mode heuristic"]
        elif amount > 2000:
            prob = 0.35
            factors = ["Medium amount", "Demo mode heuristic"]
        else:
            prob = 0.15
            factors = ["Normal amount", "Demo mode heuristic"]

        return {
            "fraud_probability": prob,
            "risk_classification": self._classify_risk(prob),
            "contributing_factors": factors,
            "scoring_timestamp": datetime.utcnow().isoformat(),
            "scoring_model": "demo_heuristic",
            "confidence": 1.0
        }

    def _classify_risk(self, probability: float) -> str:
        """Map probability to risk classification"""
        if probability < 0.30:
            return "low"
        elif probability < 0.60:
            return "medium"
        elif probability < 0.85:
            return "high"
        else:
            return "critical"

    async def close(self) -> None:
        """Close HTTP client"""
        await self.client.aclose()
