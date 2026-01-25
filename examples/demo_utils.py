"""
Demo Utilities Module

Provides reusable utilities for demo scripts including:
- Progress indicators with timing
- Actionable error messages
- Database connection helpers
- Data availability checks
- IRIS version and feature detection
- Graceful degradation for missing features

Used by demo_biomedical.py, demo_fraud_detection.py, and demo_working_system.py.
"""

import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple


# ANSI color codes for terminal output
class Colors:
    """ANSI color codes for rich terminal output."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"

    @classmethod
    def enabled(cls) -> bool:
        """Check if colors should be enabled."""
        # Disable in non-TTY environments or if NO_COLOR is set
        if os.environ.get("NO_COLOR"):
            return False
        if not sys.stdout.isatty():
            return False
        return True

    @classmethod
    def success(cls, text: str) -> str:
        if cls.enabled():
            return f"{cls.GREEN}{text}{cls.RESET}"
        return text

    @classmethod
    def error(cls, text: str) -> str:
        if cls.enabled():
            return f"{cls.RED}{text}{cls.RESET}"
        return text

    @classmethod
    def warning(cls, text: str) -> str:
        if cls.enabled():
            return f"{cls.YELLOW}{text}{cls.RESET}"
        return text

    @classmethod
    def info(cls, text: str) -> str:
        if cls.enabled():
            return f"{cls.CYAN}{text}{cls.RESET}"
        return text

    @classmethod
    def bold(cls, text: str) -> str:
        if cls.enabled():
            return f"{cls.BOLD}{text}{cls.RESET}"
        return text


class DemoError(Exception):
    """Exception with actionable next steps for demo users."""

    def __init__(self, message: str, next_steps: Optional[List[str]] = None):
        self.message = message
        self.next_steps = next_steps or []
        super().__init__(message)

    def display(self):
        """Display the error with next steps guidance."""
        print(f"\n{'='*60}")
        print(f"ERROR: {self.message}")
        if self.next_steps:
            print("\nNext Steps:")
            for i, step in enumerate(self.next_steps, 1):
                print(f"  {i}. {step}")
        print(f"{'='*60}\n")


class DemoRunner:
    """
    Base class for running demo scripts with progress tracking.

    Provides:
    - Step-by-step progress indicators
    - Timing for each operation
    - Structured error handling with next steps
    - Database connection management
    """

    def __init__(self, title: str, total_steps: int = 5):
        self.title = title
        self.total_steps = total_steps
        self.current_step = 0
        self.step_times: List[Tuple[str, float]] = []
        self.start_time: Optional[float] = None
        self.connection = None

    def start(self):
        """Display demo header and start timing."""
        print(f"\n{self.title}")
        print("=" * len(self.title))
        self.start_time = time.time()

    def step(self, description: str) -> "StepContext":
        """
        Execute a demo step with progress indicator.

        Usage:
            with runner.step("Connecting to database"):
                conn = connect()
        """
        self.current_step += 1
        return StepContext(self, self.current_step, description)

    def record_step(self, description: str, elapsed_ms: float, success: bool = True):
        """Record step completion."""
        self.step_times.append((description, elapsed_ms))
        status = "OK" if success else "FAILED"
        print(
            f"[{self.current_step}/{self.total_steps}] {description}... {status} ({elapsed_ms:.2f}ms)"
        )

    def finish(self, success: bool = True):
        """Display demo completion summary."""
        total_time = (time.time() - self.start_time) * 1000 if self.start_time else 0

        print()
        if success:
            print(f"Demo completed successfully in {total_time/1000:.2f}s")
        else:
            print(f"Demo failed after {total_time/1000:.2f}s")

        # Show timing breakdown if verbose
        if len(self.step_times) > 0:
            slowest = max(self.step_times, key=lambda x: x[1])
            print(f"Slowest step: {slowest[0]} ({slowest[1]:.2f}ms)")

    def get_connection(self):
        """
        Get database connection using iris-devtester.

        Raises DemoError with next steps if connection fails.
        """
        if self.connection:
            return self.connection

        try:
            from iris_devtester.connections import auto_detect_iris_host_and_port
            from iris_devtester.utils.dbapi_compat import get_connection as dbapi_connect

            host, port = auto_detect_iris_host_and_port()
            if port is None:
                port = 1972  # Default fallback

            self.connection = dbapi_connect(host or "localhost", port, "USER", "_SYSTEM", "SYS")
            return self.connection

        except ImportError:
            raise DemoError(
                "iris-devtester package not installed",
                next_steps=["Run: pip install iris-devtester", "Or: uv sync"],
            )
        except Exception as e:
            error_msg = str(e).lower()

            if "connection refused" in error_msg or "connect" in error_msg:
                raise DemoError(
                    "Cannot connect to IRIS database",
                    next_steps=[
                        "Start IRIS: docker-compose up -d",
                        "Or for ACORN-1: docker-compose -f docker-compose.acorn.yml up -d",
                        "Check docker ps to verify container is running",
                        f"Current error: {e}",
                    ],
                )
            elif "password" in error_msg:
                raise DemoError(
                    "IRIS password authentication failed",
                    next_steps=[
                        "Password may have expired",
                        "Run: python scripts/setup_iris.py to reset",
                        f"Current error: {e}",
                    ],
                )
            else:
                raise DemoError(
                    f"Database connection error: {e}",
                    next_steps=[
                        "Verify IRIS is running: docker ps",
                        "Check .env file has correct IRIS_PORT",
                        "Try: python scripts/setup_iris.py",
                    ],
                )

    def check_data_availability(self, label: str, min_count: int = 1) -> int:
        """
        Check if data with given label exists in database.

        Returns count of entities with label.
        Raises DemoError if count < min_count.
        """
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM rdf_labels WHERE label = ?", (label,))
        count = cursor.fetchone()[0]

        if count < min_count:
            raise DemoError(
                f"Insufficient {label} data: found {count}, need at least {min_count}",
                next_steps=[
                    'Load sample data: python -c "from scripts.setup import load_sample_data; load_sample_data()"',
                    "Or run: python sql/sample_data_768.sql",
                    "Check database connectivity: python examples/demo_working_system.py",
                ],
            )

        return count

    def check_vector_support(self) -> bool:
        """
        Check if IRIS VECTOR functions are available.

        Returns True if VECTOR_COSINE works, False otherwise.
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT VECTOR_COSINE(TO_VECTOR('[1,0,0]'), TO_VECTOR('[1,0,0]'))")
            result = cursor.fetchone()[0]
            return abs(result - 1.0) < 0.01
        except Exception:
            return False

    def check_iris_version(self) -> Optional[str]:
        """
        Get IRIS version string if available.
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT $ZVERSION")
            return cursor.fetchone()[0]
        except Exception:
            return None

    def detect_database_state(self) -> Dict[str, Any]:
        """
        Auto-detect database state including data availability and features.

        Returns dict with:
        - connected: bool
        - has_biomedical_data: bool
        - has_fraud_data: bool
        - has_embeddings: bool
        - vector_support: bool
        - iris_version: str or None
        - entity_counts: dict of label -> count
        """
        state = {
            "connected": False,
            "has_biomedical_data": False,
            "has_fraud_data": False,
            "has_embeddings": False,
            "vector_support": False,
            "iris_version": None,
            "entity_counts": {},
        }

        try:
            conn = self.get_connection()
            state["connected"] = True
            cursor = conn.cursor()

            cursor.execute("SELECT label, COUNT(*) FROM rdf_labels GROUP BY label")
            state["entity_counts"] = {row[0]: row[1] for row in cursor.fetchall()}

            biomedical_labels = ["Gene", "Protein", "Disease", "Drug", "Pathway"]
            state["has_biomedical_data"] = any(
                state["entity_counts"].get(label, 0) > 0 for label in biomedical_labels
            )

            fraud_labels = ["Account", "Transaction", "Alert"]
            state["has_fraud_data"] = any(
                state["entity_counts"].get(label, 0) > 0 for label in fraud_labels
            )

            cursor.execute("SELECT COUNT(*) FROM kg_NodeEmbeddings")
            state["has_embeddings"] = cursor.fetchone()[0] > 0

            state["vector_support"] = self.check_vector_support()
            state["iris_version"] = self.check_iris_version()

        except Exception:
            pass

        return state

    def offer_load_sample_data(self, domain: str = "biomedical") -> bool:
        """
        Offer to load sample data if missing. Returns True if user accepts.

        In non-interactive mode, prints instructions instead.
        """
        if domain == "biomedical":
            load_cmd = 'python -c "from scripts.setup import load_sample_data; load_sample_data()"'
        else:
            load_cmd = 'python -c "from scripts.setup import load_fraud_data; load_fraud_data()"'

        if sys.stdin.isatty():
            print("\n" + Colors.warning("No " + domain + " data found."))
            print("Would you like to load sample data? (y/n): ", end="")
            try:
                response = input().strip().lower()
                if response in ("y", "yes"):
                    print("Run: " + Colors.info(load_cmd))
                    return True
            except (EOFError, KeyboardInterrupt):
                pass
        else:
            print("\n" + Colors.warning("No " + domain + " data found."))
            print("To load sample data, run:")
            print("  " + Colors.info(load_cmd))

        return False

    def print_feature_status(self):
        """Print status of available IRIS features."""
        state = self.detect_database_state()

        print(f"\n{Colors.bold('IRIS Feature Status:')}")

        if state["connected"]:
            print(f"  {Colors.success('✓')} Database connected")
        else:
            print(f"  {Colors.error('✗')} Database not connected")
            return

        if state["iris_version"]:
            print(f"  {Colors.info('ℹ')} Version: {state['iris_version'][:50]}...")

        if state["vector_support"]:
            print(f"  {Colors.success('✓')} VECTOR functions available")
        else:
            print(f"  {Colors.warning('!')} VECTOR functions unavailable (requires IRIS 2025.1+)")

        if state["has_embeddings"]:
            print(f"  {Colors.success('✓')} Vector embeddings loaded")
        else:
            print(f"  {Colors.warning('!')} No vector embeddings")

        if state["has_biomedical_data"]:
            print(f"  {Colors.success('✓')} Biomedical data available")
        else:
            print(f"  {Colors.warning('!')} No biomedical data")

        if state["has_fraud_data"]:
            print(f"  {Colors.success('✓')} Fraud detection data available")
        else:
            print(f"  {Colors.warning('!')} No fraud detection data")


class StepContext:
    """Context manager for demo step execution with timing."""

    def __init__(self, runner: DemoRunner, step_num: int, description: str):
        self.runner = runner
        self.step_num = step_num
        self.description = description
        self.start_time: Optional[float] = None
        self.result: Any = None

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        start = self.start_time if self.start_time is not None else time.time()
        elapsed_ms = (time.time() - start) * 1000

        if exc_type is not None:
            # Handle DemoError specially
            if isinstance(exc_val, DemoError):
                self.runner.record_step(self.description, elapsed_ms, success=False)
                exc_val.display()
                return False  # Re-raise
            else:
                self.runner.record_step(self.description, elapsed_ms, success=False)
                return False  # Re-raise

        self.runner.record_step(self.description, elapsed_ms, success=True)
        return False


def format_count(count: int, singular: str, plural: Optional[str] = None) -> str:
    """Format a count with proper singular/plural form."""
    plural_form = plural if plural is not None else singular + "s"
    return f"{count:,} {singular if count == 1 else plural_form}"


def display_results_table(headers: List[str], rows: List[List[Any]], max_width: int = 80):
    """
    Display a simple ASCII table of results.

    Args:
        headers: Column headers
        rows: List of row data (list of values per row)
        max_width: Maximum width before truncation
    """
    # Calculate column widths
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(val)))

    # Truncate if necessary
    total_width = sum(col_widths) + 3 * len(headers) + 1
    if total_width > max_width:
        scale = max_width / total_width
        col_widths = [int(w * scale) for w in col_widths]

    # Print header
    header_row = " | ".join(h.ljust(w)[:w] for h, w in zip(headers, col_widths))
    print(f"| {header_row} |")
    print("|" + "|".join("-" * (w + 2) for w in col_widths) + "|")

    # Print rows
    for row in rows:
        row_str = " | ".join(str(v).ljust(w)[:w] for v, w in zip(row, col_widths))
        print(f"| {row_str} |")
