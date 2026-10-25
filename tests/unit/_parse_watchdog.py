"""Run a parse in a child process under a memory and time watchdog.

A parser defect that loops while appending to a list eats all RAM in
under two minutes, and macOS ignores ``RLIMIT_AS``, so the protection is
a supervisor that polls the child's resident set size and kills it.

Never call the parser on a suspect query in the calling process; use
``run_guarded_parse`` (a child process per query).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from dataclasses import dataclass

REPO_ROOT = Path(__file__).resolve().parents[2]
MEM_LIMIT_MB = 500
TIME_LIMIT_S = 5.0
POLL_S = 0.1

_PARSE_CHILD = r"""
import json, sys
from iris_vector_graph.cypher.parser import parse_query
q = sys.argv[1]
try:
    ast = parse_query(q)
except Exception as exc:  # a parse error is an acceptable outcome
    print(json.dumps({"outcome": "error", "type": type(exc).__name__, "message": str(exc)}))
else:
    def shape(c):
        return {
            "parts": len(c.query_parts),
            "withs": sum(1 for p in c.query_parts if p.with_clause is not None),
            "has_return": c.return_clause is not None,
            "graph": c.graph_context,
            "skip": c.skip,
            "limit": c.limit,
        }
    print(json.dumps({
        "outcome": "ok",
        "main": shape(ast),
        "unions": [dict(all=u["all"], **shape(u["query"])) for u in ast.union_queries],
    }, default=str))
"""

_ALLOC_CHILD = r"""
chunks = []
while True:
    chunks.append(bytearray(10 * 1024 * 1024))
    for c in chunks[-1:]:
        c[::4096] = b"x" * len(c[::4096])
"""

_SPIN_CHILD = r"""
while True:
    pass
"""


@dataclass
class GuardResult:
    status: str  # "done" | "KILLED: memory" | "KILLED: time"
    peak_rss_mb: float
    elapsed_s: float
    stdout: str = ""
    stderr: str = ""

    @property
    def killed(self) -> bool:
        return self.status.startswith("KILLED")

    def payload(self) -> dict:
        return json.loads(self.stdout.strip().splitlines()[-1])


def _rss_mb(proc: subprocess.Popen) -> float:
    try:
        import psutil  # type: ignore
    except ImportError:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(proc.pid)],
            capture_output=True,
            text=True,
        ).stdout.strip()
        return int(out) / 1024 if out else 0.0
    try:
        return psutil.Process(proc.pid).memory_info().rss / (1024 * 1024)
    except psutil.Error:  # process already gone
        return 0.0


def run_guarded(
    code: str,
    arg: str = "",
    mem_limit_mb: float = MEM_LIMIT_MB,
    time_limit_s: float = TIME_LIMIT_S,
) -> GuardResult:
    """Run child code; kill it above the RSS limit or after the time limit."""
    proc = subprocess.Popen(
        [sys.executable, "-c", code, arg],
        stdin=subprocess.DEVNULL,
        cwd=REPO_ROOT,  # so the child imports this checkout's parser
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    start = time.monotonic()
    peak = 0.0
    status = "done"
    while proc.poll() is None:
        rss = _rss_mb(proc)
        peak = max(peak, rss)
        if rss > mem_limit_mb:
            status = "KILLED: memory"
            break
        if time.monotonic() - start > time_limit_s:
            status = "KILLED: time"
            break
        time.sleep(POLL_S)
    if status != "done":
        proc.kill()
    out, err = proc.communicate()
    return GuardResult(status, peak, time.monotonic() - start, out, err)


def run_guarded_parse(query: str, **kw) -> GuardResult:
    """Parse ``query`` with the project parser in a watched child process."""
    return run_guarded(_PARSE_CHILD, arg=query, **kw)


def self_test_alloc(**kw) -> GuardResult:
    """A harmless runaway allocation: the watchdog must kill it."""
    return run_guarded(_ALLOC_CHILD, **kw)


def self_test_spin(**kw) -> GuardResult:
    """A runaway CPU loop: the watchdog must kill it on time."""
    return run_guarded(_SPIN_CHILD, **kw)
