#!/usr/bin/env python3
"""
Unified Test Runner for IRIS Vector Graph

Usage:
    run-tests                    # All tests
    run-tests unit               # Unit tests only
    run-tests integration        # Integration tests
    run-tests e2e                # End-to-end tests
    run-tests ux                 # UX tests (auto-starts demo)
    run-tests --quick            # Skip slow tests
    run-tests unit -- -x --pdb   # Pytest passthrough

DEPRECATED: tests/python/run_all_tests.py
"""

import argparse
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

DEFAULT_DEMO_PORT = 8200
DEMO_STARTUP_TIMEOUT = 30.0
HEALTH_CHECK_INTERVAL = 0.5

EXIT_DEMO_FAILED = 10
EXIT_HEALTH_TIMEOUT = 11


@dataclass
class DemoServerManager:
    port: int = DEFAULT_DEMO_PORT
    host: str = "127.0.0.1"
    startup_timeout: float = DEMO_STARTUP_TIMEOUT
    
    _process: subprocess.Popen | None = field(default=None, repr=False)
    
    def __enter__(self) -> "DemoServerManager":
        self._start()
        self._wait_for_ready()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._shutdown()
    
    def _start(self) -> None:
        project_root = Path(__file__).parent.parent
        src_path = str(project_root / "src")
        
        env = os.environ.copy()
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{src_path}:{existing_pythonpath}" if existing_pythonpath else src_path
        
        logger.info(f"Starting demo server on {self.host}:{self.port}...")
        
        self._process = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn",
                "iris_demo_server.app:app",
                "--host", self.host,
                "--port", str(self.port),
                "--log-level", "warning",
            ],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    
    def _wait_for_ready(self) -> None:
        url = f"http://{self.host}:{self.port}/"
        deadline = time.time() + self.startup_timeout
        
        while time.time() < deadline:
            try:
                response = httpx.get(url, timeout=2.0)
                if response.status_code == 200:
                    logger.info("Demo server ready")
                    return
            except httpx.RequestError:
                pass
            time.sleep(HEALTH_CHECK_INTERVAL)
        
        self._shutdown()
        raise RuntimeError(
            f"Demo server failed to start within {self.startup_timeout}s. "
            f"Check that src/ is in PYTHONPATH and iris_demo_server.app exists."
        )
    
    def _shutdown(self) -> None:
        if self._process is None:
            return
        
        logger.info("Shutting down demo server...")
        self._process.terminate()
        
        try:
            self._process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            logger.warning("Demo server did not respond to SIGTERM, forcing kill")
            self._process.kill()
            self._process.wait()
        
        logger.info(f"Demo server exited with code {self._process.returncode}")


class TestExecutor:
    CATEGORY_CONFIG = {
        "unit": {"marker": "not (requires_database or e2e or integration)", "paths": ["tests/unit"]},
        "integration": {"marker": "integration or requires_database", "paths": ["tests/integration", "tests/python"]},
        "e2e": {"marker": "e2e", "paths": ["tests/e2e"]},
        "ux": {"marker": "e2e", "paths": ["tests/e2e"], "pattern": "*_ui.py"},
        "contract": {"marker": None, "paths": ["tests/contract"]},
    }
    
    def __init__(self, categories: list[str], pytest_args: list[str]):
        self.categories = categories if categories else ["all"]
        self.pytest_args = pytest_args
    
    def build_command(self) -> list[str]:
        cmd = [sys.executable, "-m", "pytest"]
        
        if "all" in self.categories:
            cmd.append("tests/")
        else:
            markers = []
            paths = set()
            
            for cat in self.categories:
                if cat not in self.CATEGORY_CONFIG:
                    raise ValueError(f"Unknown category: {cat}")
                
                config = self.CATEGORY_CONFIG[cat]
                if config["marker"]:
                    markers.append(f"({config['marker']})")
                paths.update(config["paths"])
            
            if markers:
                cmd.extend(["-m", " or ".join(markers)])
            
            cmd.extend(sorted(paths))
        
        cmd.extend(self.pytest_args)
        
        return cmd
    
    def run(self) -> int:
        cmd = self.build_command()
        logger.info(f"Running: {' '.join(cmd)}")
        
        result = subprocess.run(cmd)
        return result.returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified Test Runner for IRIS Vector Graph",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Categories:
  unit          Unit tests (no database required)
  integration   Integration tests (requires IRIS)
  e2e           End-to-end tests (full stack)
  ux            UX tests (auto-starts demo server)
  contract      API contract tests

Examples:
  run-tests                    # All tests
  run-tests unit               # Unit tests only
  run-tests integration e2e    # Multiple categories
  run-tests --quick            # Skip slow tests
  run-tests unit -- -x --pdb   # Pytest passthrough
        """
    )
    
    parser.add_argument(
        "categories",
        nargs="*",
        choices=["unit", "integration", "e2e", "ux", "contract", "all"],
        default=["all"],
        help="Test categories to run (default: all)"
    )
    
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run unit + integration only (skip e2e, ux)"
    )
    
    parser.add_argument(
        "--demo-server",
        action="store_true",
        help="Start demo server for tests"
    )
    
    parser.add_argument(
        "--no-demo-server",
        action="store_true",
        help="Don't start demo server (assume already running)"
    )
    
    parser.add_argument(
        "--demo-port",
        type=int,
        default=DEFAULT_DEMO_PORT,
        help=f"Demo server port (default: {DEFAULT_DEMO_PORT})"
    )
    
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output"
    )
    
    args, pytest_args = parser.parse_known_args()
    
    if pytest_args and pytest_args[0] == "--":
        pytest_args = pytest_args[1:]
    
    args.pytest_args = pytest_args
    
    if args.quick:
        args.categories = ["unit", "integration"]
    
    return args


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s"
    )
    
    args = parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    needs_demo = args.demo_server or (
        ("ux" in args.categories or "all" in args.categories)
        and not args.no_demo_server
    )
    
    try:
        if needs_demo:
            logger.info("Demo server required for UX tests")
            with DemoServerManager(port=args.demo_port):
                return TestExecutor(args.categories, args.pytest_args).run()
        else:
            return TestExecutor(args.categories, args.pytest_args).run()
    
    except RuntimeError as e:
        logger.error(str(e))
        return EXIT_DEMO_FAILED
    
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 130


if __name__ == "__main__":
    sys.exit(main())
