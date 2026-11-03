"""4.6.0 (NR7): the report's Acceleration line says which engine answers traversals.

Before 4.6.0 it printed `Acceleration:    ready` whenever ^NKG was populated, while `status().arno` was
`ArnoStatus(loaded=False)` and every BFS ran in ObjectScript.
"""

from __future__ import annotations

from iris_vector_graph.status import AdjacencyStatus, ArnoStatus, EngineStatus


def _line(status: EngineStatus) -> str:
    return next(line for line in status.report().splitlines() if line.startswith("Acceleration:"))


def test_arno_not_loaded_says_objectscript():
    s = EngineStatus(
        adjacency=AdjacencyStatus(kg_populated=True, nkg_populated=True, bfs_path="objectscript"),
        arno=ArnoStatus(loaded=False),
    )
    assert _line(s) == "Acceleration:    ObjectScript (Arno not loaded)"


def test_arno_not_loaded_on_an_empty_graph_says_objectscript():
    assert _line(EngineStatus(arno=ArnoStatus(loaded=False))) == "Acceleration:    ObjectScript (Arno not loaded)"


def test_arno_loaded_names_its_version():
    s = EngineStatus(
        adjacency=AdjacencyStatus(kg_populated=True, nkg_populated=True, bfs_path="arno"),
        arno=ArnoStatus(loaded=True, version="arno-callout 1.2.0+3f9e2c1"),
    )
    assert _line(s) == "Acceleration:    Arno 1.2.0+3f9e2c1"


def test_arno_loaded_without_a_version_string_says_arno():
    s = EngineStatus(arno=ArnoStatus(loaded=True))
    assert _line(s) == "Acceleration:    Arno"
