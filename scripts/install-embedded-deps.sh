#!/usr/bin/env bash
# Install igraph + leidenalg into an IRIS container's EMBEDDED Python (mgr/python)
# so the fast closeness (ClosenessJsonPy) and canonical Leiden (LeidenJsonAuto)
# paths fire instead of falling back to slow ObjectScript / networkx Louvain.
# Spec 191 FR-A1/FR-A2. Idempotent. Pins igraph+leidenalg ONLY — never touches
# intersystems-irispython (AGENTS.md embedded-Python rule).
# Usage: scripts/install-embedded-deps.sh <container-name>
set -euo pipefail
CONTAINER="${1:?usage: install-embedded-deps.sh <container>}"

PYBIN="/usr/irissys/bin/irispython"
TARGET="/usr/irissys/mgr/python"

if docker exec "$CONTAINER" "$PYBIN" -c "import igraph, leidenalg" >/dev/null 2>&1; then
  echo "✓ igraph + leidenalg already installed in $CONTAINER embedded Python"
else
  echo "Installing igraph + leidenalg into $CONTAINER embedded Python ($TARGET)..."
  # Pin to these two packages only; do NOT --upgrade — that could pull in a new
  # intersystems-iris* wheel and break the embedded runtime (AGENTS.md hard rule).
  docker exec "$CONTAINER" "$PYBIN" -m pip install --target "$TARGET" \
    "igraph>=1.0" "leidenalg>=0.10" 2>&1 | grep -iE 'Successfully|already satisfied|ERROR' | head -5 || true
fi

# Post-condition (FR-A2): the embedded fast paths must report OK:, not PYUNAVAIL.
echo "Verifying embedded fast paths..."
docker exec -i "$CONTAINER" iris session iris -U USER <<'OSEOF' 2>/dev/null | grep -E 'CLOSENESS|LEIDEN' || true
set cl = $extract(##class(Graph.KG.Communities).ClosenessJsonPy("harmonic", 1), 1, 12)
write "CLOSENESS: ", $select(cl["OK:":"OK", cl="PYUNAVAIL":"PYUNAVAIL", 1:cl), !
set ld = $extract(##class(Graph.KG.Communities).LeidenJsonAuto(10, 1.0, 0.0001, 1, 256, -1), 1, 12)
write "LEIDEN: ", $select(ld["OK:":"OK", ld="PYUNAVAIL":"PYUNAVAIL", 1:ld), !
halt
OSEOF
