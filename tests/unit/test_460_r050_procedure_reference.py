"""4.6.0 R050 (Python-newcomer walkthrough W5): the Cypher procedures the User Guide names have a reference.

The guide said "Each has a Cypher form too" and showed one call; the API Reference listed no procedure at all, so a
reader guessed `YIELD node, score` and the option names, and learned `ivg.closeness`'s options from an error. The
User Guide's "Cypher procedures" table lists each centrality, PageRank and path procedure with its arguments, options
and YIELD columns; this checks the table against what the code accepts (the translator's allowed keys, the gds.*
configuration keys and yields), and that the API Reference points to it and to COMMUNITIES.md's table.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from iris_vector_graph._engine.query import GDS_YIELDS
from iris_vector_graph.cypher.translator import CENTRALITY_ALLOWED_KEYS, COMMUNITY_ALLOWED_KEYS
from iris_vector_graph.engine import IRISGraphEngine

DOCS = Path(__file__).resolve().parents[2] / "docs"


def _table(path: Path, heading: str) -> dict:
    """The rows of the first table under ``heading``: procedure -> (arguments, options, yields), each a set of names
    in backticks."""
    text = path.read_text(encoding="utf-8").split(f"\n{heading}\n", 1)[1]
    rows = {}
    for line in text.splitlines():
        if not line.startswith("| `"):
            if rows:
                break
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        names = [set(re.findall(r"`([^`]+)`", c)) for c in cells]
        rows[cells[0].strip("`")] = tuple(names[1:]) if len(names) == 4 else (set(),) + tuple(names[1:])
    return rows


@pytest.fixture(scope="module")
def guide():
    return _table(DOCS / "USER_GUIDE.md", "### Cypher procedures")


@pytest.mark.parametrize("proc", sorted(CENTRALITY_ALLOWED_KEYS))
def test_each_centrality_procedure_is_listed_with_its_options(guide, proc):
    assert proc in guide, sorted(guide)
    _, options, yields = guide[proc]
    assert options == CENTRALITY_ALLOWED_KEYS[proc]
    assert {"node", "score"} <= yields


@pytest.mark.parametrize("proc", ["gds.pageRank.stream", "gds.betweenness.stream", "gds.shortestPath.dijkstra.stream"])
def test_each_gds_procedure_is_listed_with_its_configuration_and_yields(guide, proc):
    assert proc in guide, sorted(guide)
    _, options, yields = guide[proc]
    assert options == set(IRISGraphEngine._GDS_CONFIG[proc.lower()])
    assert yields == {y.strip() for y in GDS_YIELDS[proc.lower()].split(",")}


def test_ppr_and_weighted_shortest_path_are_listed(guide):
    assert guide["ivg.ppr"][2] == {"node", "score"}
    assert guide["ivg.shortestPath.weighted"][2] == {"path", "totalCost"}


def test_the_community_table_lists_the_community_procedures():
    table = _table(DOCS / "COMMUNITIES.md", "## Cypher procedures")
    assert {p: o for p, (_, o, _) in table.items()} == COMMUNITY_ALLOWED_KEYS


def test_the_api_reference_points_to_both_tables():
    ref = (DOCS / "API_REFERENCE.md").read_text(encoding="utf-8")
    assert "(USER_GUIDE.md#cypher-procedures)" in ref
    assert "(COMMUNITIES.md#cypher-procedures)" in ref
