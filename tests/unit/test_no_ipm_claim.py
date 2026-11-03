"""4.6.0 (NR13): IVG makes no IPM / ZPM install claim.

The README's feature table advertised an "ObjectScript-only install via InterSystems Package Manager", with no doc
saying how. `module.xml` (version 2.0.0) and `module-core.xml` (1.27.0) listed three classes that no longer exist
(`Graph.KG.Edge`, `Graph.KG.Loader`, `Graph.KG.BenchSeeder`). The install is `pip install iris-vector-graph` and
`initialize_schema()`.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_no_ipm_manifests():
    assert not (ROOT / "module.xml").exists()
    assert not (ROOT / "module-core.xml").exists()


def test_the_readme_feature_table_has_no_ipm_row():
    rows = [line for line in (ROOT / "README.md").read_text().splitlines() if line.startswith("|")]
    assert rows, "no table rows found in README.md"
    assert [r for r in rows if re.search(r"\b(IPM|ZPM)\b", r)] == []
