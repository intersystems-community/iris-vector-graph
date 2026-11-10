#!/usr/bin/env bash
# Install into an IRIS container's EMBEDDED Python (mgr/python):
#   - igraph + leidenalg, so the fast closeness (ClosenessJsonPy) and canonical Leiden (LeidenJsonAuto) paths fire
#     instead of falling back to slow ObjectScript / networkx Louvain (spec 191 FR-A1/FR-A2);
#   - IVG's own import-time dependencies (numpy, pydantic, requests: pyproject.toml's core list), so IVG imports
#     inside IRIS: IVG.CypherEngine.Local(), Graph.KG.MCPTools, and the E2E tests that run the checkout with
#     irispython (test_embedded_irispython_e2e, test_host_conformance). R234: a fresh container had no numpy there.
# Idempotent. Never installs intersystems-iris / intersystems-irispython / iris-embedded-python-wrapper: each writes a
# top-level `iris` module, and one in mgr/python shadows IRIS's own (AGENTS.md embedded-Python rule).
# tests/unit/test_install_embedded_deps_covers_core.py keeps the list in step with pyproject.toml.
# Usage: scripts/install-embedded-deps.sh <container-name>
set -euo pipefail
CONTAINER="${1:?usage: install-embedded-deps.sh <container>}"

PYBIN="/usr/irissys/bin/irispython"
TARGET="/usr/irissys/mgr/python"

if docker exec "$CONTAINER" "$PYBIN" -c "import igraph, leidenalg, numpy, pydantic, requests" >/dev/null 2>&1; then
  echo "✓ igraph, leidenalg and IVG's dependencies already installed in $CONTAINER embedded Python"
else
  echo "Installing igraph, leidenalg, numpy, pydantic, requests into $CONTAINER embedded Python ($TARGET)..."
  # Named packages only; do NOT --upgrade — that could pull in a new intersystems-iris* wheel and break the
  # embedded runtime (AGENTS.md hard rule).
  docker exec "$CONTAINER" "$PYBIN" -m pip install --target "$TARGET" \
    "igraph>=1.0" "leidenalg>=0.10" "numpy>=1.24.0" "pydantic>=2.0.0" "requests>=2.28.0" "tzdata>=2025.2" 2>&1 \
    | grep -iE 'Successfully|already satisfied|ERROR' | head -5 || true
fi

# Post-condition: IVG's dependencies import inside IRIS, and the embedded fast paths report OK:, not PYUNAVAIL (FR-A2).
echo "Verifying embedded Python..."
if docker exec "$CONTAINER" "$PYBIN" -c "import numpy, pydantic, requests" >/dev/null 2>&1; then
  echo "IVG DEPENDENCIES: OK"
else
  echo "IVG DEPENDENCIES: MISSING (numpy, pydantic or requests does not import in $PYBIN)"
fi
docker exec -i "$CONTAINER" iris session iris -U USER <<'OSEOF' 2>/dev/null | grep -E 'CLOSENESS|LEIDEN' || true
set cl = $extract(##class(Graph.KG.Communities).ClosenessJsonPy("harmonic", 1), 1, 12)
write "CLOSENESS: ", $select(cl["OK:":"OK", cl="PYUNAVAIL":"PYUNAVAIL", 1:cl), !
set ld = $extract(##class(Graph.KG.Communities).LeidenJsonAuto(10, 1.0, 0.0001, 1, 256, -1), 1, 12)
write "LEIDEN: ", $select(ld["OK:":"OK", ld="PYUNAVAIL":"PYUNAVAIL", 1:ld), !
halt
OSEOF
