"""Classes deployed into a pre-4.0.0 namespace must compile there.

A static ``&sql`` on ``rdf_labels`` / ``rdf_props`` that names ``graph_id`` cannot compile while those tables still have the 3.2.0
shape (no such column). The deploy then leaves the class half-compiled, and the migration's own ``ALTER TABLE rdf_labels`` waits
out IRIS's 120 s lock timeout on it: a 50,000-node upgrade took 182 s instead of 62 s, every time. The two classes affected,
``Subgraph`` and ``TraversalBuild``, read those tables with dynamic SQL, which is compiled when run (after the migration), not when
deployed. The baseline has the same two compile errors in a fresh legacy namespace; this removes them.
"""

import re
from pathlib import Path

CLS = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG"
CLASSES = ("Subgraph.cls", "TraversalBuild.cls")


def static_sql_statements(text):
    """Each ``&sql(...)`` statement's text, from the opening to the end of its line (every one of these is one line)."""
    return [m.group(0) for m in re.finditer(r"&sql\(.*", text)]


def test_no_static_sql_on_the_tables_that_gain_graph_id_in_the_migration():
    for name in CLASSES:
        text = (CLS / name).read_text(encoding="utf-8")
        code = "\n".join(line.split("//", 1)[0] for line in text.splitlines())
        offenders = [s for s in static_sql_statements(code) if re.search(r"rdf_labels|rdf_props", s)]
        assert not offenders, f"{name} has static SQL that cannot compile before the migration: {offenders}"


def test_the_dynamic_replacements_still_read_both_tables_and_the_graph_column():
    build = (CLS / "TraversalBuild.cls").read_text(encoding="utf-8")
    assert re.search(r"%Prepare\([^)]*FROM Graph_KG\.rdf_labels", build)
    # spec 252: BuildKG no longer refills ^KG("prop"); Subgraph reads rdf_props itself
    assert not re.search(r"FROM Graph_KG\.rdf_props", build)
    sub = (CLS / "Subgraph.cls").read_text(encoding="utf-8")
    assert re.search(r"%Prepare\([^)]*FROM Graph_KG\.rdf_props WHERE s = \? AND COALESCE\(graph_id, ''\) = COALESCE\(\?, ''\)", sub)
    assert re.search(r"%Prepare\([^)]*FROM Graph_KG\.rdf_labels WHERE s = \? AND COALESCE\(graph_id, ''\) = COALESCE\(\?, ''\)", sub)
