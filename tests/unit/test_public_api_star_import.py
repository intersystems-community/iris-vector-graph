"""`from iris_vector_graph import *` works, and every name in `__all__` is one the package has (R085).

`__all__` once listed the API middleware (`ApiKeyMiddleware`, `ReadOnlyMiddleware`, `is_mutation_cypher`), which the
package never imported, so a star import raised AttributeError. Run in a subprocess so the import is fresh: a name
another test happened to import as a submodule attribute must not make it pass.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

_PROBE = """
import iris_vector_graph as pkg
from iris_vector_graph import *  # noqa: F403
missing = [n for n in pkg.__all__ if n not in globals()]
assert not missing, f"in __all__ but not importable: {missing}"
print("OK", len(pkg.__all__))
"""


def test_star_import_succeeds_and_every_name_in_all_resolves():
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO), os.environ.get("PYTHONPATH")]))}
    proc = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True, text=True, env=env, cwd=REPO,
                          timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("OK "), proc.stdout


def test_the_api_middleware_stays_importable_from_api_auth():
    """Its documented home: dropping it from the package's `__all__` must not drop it."""
    from iris_vector_graph.api_auth import ApiKeyMiddleware, ReadOnlyMiddleware, is_mutation_cypher

    assert is_mutation_cypher("CREATE (n) RETURN n")
    assert not is_mutation_cypher("MATCH (n) RETURN n")
    assert ApiKeyMiddleware.__name__ == "ApiKeyMiddleware"
    assert ReadOnlyMiddleware.__name__ == "ReadOnlyMiddleware"
