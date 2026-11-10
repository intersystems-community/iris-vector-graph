"""4.6.5 round 9: the six positional-argument procedures refuse an options map (naming their positional arguments).

USER_GUIDE documents `ivg.ppr`, `ivg.shortestPath.weighted`, `ivg.bm25.search`, `ivg.ivf.search`, `ivg.retrieve` and
`ivg.neighbors` as taking positional arguments, with "Options: none" in the procedure table. A newcomer who wrote
`CALL ivg.ppr({seeds: ['alice']})` got "requires at least 1 argument (seed_ids)", which names something the docs never
mention. The parser puts a map into `proc.options`, which ppr never reads: it is silently ignored. That is a silent
wrong answer when a user tries (as the docs show) `CALL ivg.ppr(['a'], {alpha: 0.5})`, expecting the second argument
to be read as options.

Now each of these refuses an options map with a ValueError naming the procedure and its positional arguments as the docs
name them. tests/e2e/test_465_positional_procedures_e2e.py checks the end-to-end contract (the refusal, and ppr still
answering a correct ranking).
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql


def _translate(query, params=None):
    """The translated SQL text."""
    sql = translate_to_sql(parse_query(query), params or {}).sql
    return sql if isinstance(sql, str) else "\n".join(sql)


# Each procedure, the call with an options map, and the positional argument names USER_GUIDE documents
OPTIONS_MAP_CALLS = [
    ("ivg.ppr", "CALL ivg.ppr({seeds: ['a'], alpha: 0.5}) YIELD node, score RETURN node", ["seeds", "alpha", "maxIter"]),
    ("ivg.ppr", "CALL ivg.ppr(['a'], {alpha: 0.5}) YIELD node, score RETURN node", ["seeds", "alpha", "maxIter"]),
    (
        "ivg.shortestPath.weighted",
        "CALL ivg.shortestPath.weighted({from: 'a', to: 'b'}) YIELD path, totalCost RETURN path",
        ["from", "to", "weightProp", "maxCost", "maxHops", "direction", "types"],
    ),
    (
        "ivg.bm25.search",
        "CALL ivg.bm25.search({name: 'idx', query: 'q', k: 10}) YIELD node, score RETURN node",
        ["name", "query", "k"],
    ),
    (
        "ivg.ivf.search",
        "CALL ivg.ivf.search({name: 'idx', query_vec: [1.0], k: 10, nprobe: 2}) YIELD node, score RETURN node",
        ["name", "query_vec", "k", "nprobe"],
    ),
    (
        "ivg.retrieve",
        "CALL ivg.retrieve({query: 'q', limit: 10}) YIELD node, rrf_score RETURN node",
        ["query", "limit", "bm25_name", "vec_label", "rrf_k", "embedding_config"],
    ),
    (
        "ivg.neighbors",
        "CALL ivg.neighbors({source_ids: ['a']}) YIELD neighbor RETURN neighbor",
        ["source_ids", "predicate", "direction"],
    ),
]


@pytest.mark.parametrize("proc,query,args", OPTIONS_MAP_CALLS)
def test_a_positional_procedure_refuses_an_options_map_naming_its_arguments(proc, query, args):
    """The message names the procedure and the positional arguments USER_GUIDE documents."""
    with pytest.raises(ValueError) as caught:
        _translate(query)
    message = str(caught.value)
    assert proc in message, f"procedure name missing from: {message}"
    assert "takes positional arguments" in message, f"hint missing from: {message}"
    assert "not an options map" in message, f"refusal missing from: {message}"
    # all argument names from USER_GUIDE must be in the message
    for arg in args:
        assert arg in message, f"argument {arg!r} missing from: {message}"


@pytest.mark.parametrize("proc,query,_", OPTIONS_MAP_CALLS)
def test_the_procedure_still_translates_with_positional_arguments(proc, query, _):
    """A correct positional call still works."""
    if proc == "ivg.ppr":
        sql = _translate("CALL ivg.ppr(['a'], 0.85, 20) YIELD node, score RETURN node")
        assert "kg_PPR" in sql
    elif proc == "ivg.shortestPath.weighted":
        sql = _translate("CALL ivg.shortestPath.weighted('a', 'b') YIELD path, totalCost RETURN path")
        assert "DijkstraPath" in sql
    elif proc == "ivg.bm25.search":
        sql = _translate("CALL ivg.bm25.search('idx', 'q', 10) YIELD node, score RETURN node")
        assert "kg_BM25" in sql
    elif proc == "ivg.ivf.search":
        sql = _translate("CALL ivg.ivf.search('idx', [1.0], 10, 2) YIELD node, score RETURN node")
        assert "kg_IVF" in sql
    elif proc == "ivg.retrieve":
        sql = _translate("CALL ivg.retrieve('q', 10) YIELD node, rrf_score RETURN node")
        assert "kg_BM25" in sql
    elif proc == "ivg.neighbors":
        sql = _translate("CALL ivg.neighbors(['a']) YIELD neighbor RETURN neighbor")
        assert "rdf_edges" in sql


def test_shortestPath_weighted_refuses_an_options_map_through_the_handler():
    """The engine handler also refuses an options map, as the translator does."""
    from unittest.mock import MagicMock
    from iris_vector_graph import IRISGraphEngine
    eng = IRISGraphEngine(MagicMock(), embedding_dimension=4)
    with pytest.raises(ValueError, match=r"ivg\.shortestPath\.weighted.*takes positional arguments.*from.*to.*weightProp"):
        eng.execute_cypher("CALL ivg.shortestPath.weighted({from: 'a', to: 'b'}) YIELD path RETURN path", raise_on_error=True)


def test_shortestPath_weighted_refuses_fewer_than_two_positional_arguments():
    """The handler refuses missing from/to with the translator's message."""
    from unittest.mock import MagicMock
    from iris_vector_graph import IRISGraphEngine
    eng = IRISGraphEngine(MagicMock(), embedding_dimension=4)
    with pytest.raises(ValueError, match=r"ivg\.shortestPath\.weighted requires at least 2 arguments: from, to"):
        eng.execute_cypher("CALL ivg.shortestPath.weighted('a') YIELD path RETURN path", raise_on_error=True)
    with pytest.raises(ValueError, match=r"ivg\.shortestPath\.weighted requires at least 2 arguments: from, to"):
        eng.execute_cypher("CALL ivg.shortestPath.weighted() YIELD path RETURN path", raise_on_error=True)
