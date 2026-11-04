"""4.6.0 R254: a variable-length named path whose start is pinned by id goes to the engine's BFS path route only when
the RETURN is what that route answers (fixes29). The answers are pinned live, on 2026.1 and 2026.3 with the native
planner on and off, in tests/e2e/test_460_r254_var_length_path_elements_e2e.py.

The route builds its columns from bare ``length`` / ``nodes`` / ``relationships`` of the path, the path and its ends:
``[r IN relationships(p) | r.km]`` came back as node columns. Such a RETURN now expands in SQL, which reads the
path's elements (CY_VLP_PATHS).
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

P = "MATCH p = (x {node_id: 'a'})-[:ROAD*1..2]->(z) "


def _routed(cypher: str) -> bool:
    return bool(translate_to_sql(parse_query(cypher)).var_length_paths)


@pytest.mark.parametrize(
    "ret",
    [
        "[r IN relationships(p) | r.km] AS v",
        "[r IN relationships(p) | type(r)] AS t",
        "size(relationships(p)) AS s",
        "[n IN nodes(p) | n.name] AS v",
        "length(p) AS l, [r IN relationships(p) | r.km] AS v",
    ],
)
def test_a_read_of_the_paths_elements_expands_in_sql(ret):
    assert not _routed(P + "RETURN " + ret)


@pytest.mark.parametrize("ret", ["p", "length(p) AS l", "nodes(p) AS ns, relationships(p) AS rs", "z.name, length(p)"])
def test_what_the_path_route_answers_still_goes_there(ret):
    assert _routed(P + "RETURN " + ret)
