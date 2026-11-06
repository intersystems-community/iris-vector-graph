"""4.6.0 security sweep: a map projection entry the parser does not take raised nothing and looped forever.

`writes_cypher` parses every HTTP and Bolt query before it runs, so one such query pinned a server worker. Each case
runs in a child process with a deadline, so a regression fails here instead of hanging the suite.
"""
import subprocess
import sys

import pytest

CASES = [
    "MATCH (n) RETURN n {.name, a: 1}",
    "MATCH (n) RETURN n {a: 1, .name}",
    "MATCH (n) RETURN n {.name",
    "MATCH (n) RETURN n {.name, 1}",
]

_CHILD = """
import sys
from iris_vector_graph.cypher.parser import parse_query
try:
    parse_query(sys.argv[1])
    print("parsed")
except Exception as e:
    print("raised", type(e).__name__)
"""


@pytest.mark.parametrize("query", CASES)
def test_map_projection_parse_ends(query):
    r = subprocess.run([sys.executable, "-c", _CHILD, query], capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stderr[-500:]
    assert r.stdout.split()[0] in ("parsed", "raised")


def test_map_projection_still_parses():
    from iris_vector_graph.cypher.parser import parse_query

    text = repr(parse_query("MATCH (n) RETURN n {.name, .age}"))
    assert "MapProjection(variable='n', keys=[('.name', None), ('.age', None)])" in text
