"""4.6.5: a ``CALL ivg.*`` / ``gds.*`` procedure refuses an option the engine method of the same name refuses (round 8).

On 4.6.4 ``CALL ivg.closeness({formula: 'classic'})`` (and ``'bogus'``, and ``1``) answered the harmonic scores,
``{maxHops: -1}`` answered every score 0.0, and ``closeness_centrality(formula="classic")`` raised: the procedure read
its options with ``int()``/``str()`` and never ran them through the models in ``_validate.py`` that the method runs.
Each procedure now runs the same model, so the Cypher form and the Python form take the same values, and a refusal is a
``ValueError`` that names the procedure and the option. This file checks the refusals offline, on the translated query;
tests/e2e/test_465_procedure_options_e2e.py checks the answers a procedure gives for the values both forms accept.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.parser import parse_query
from iris_vector_graph.cypher.translator import translate_to_sql

YIELD = "YIELD node, score RETURN node, score"

#: the procedures that take one options map and no positional argument, with the YIELD each one has
MAP_PROCEDURES = {
    "ivg.degreeCentrality": YIELD,
    "ivg.betweenness": YIELD,
    "ivg.closeness": YIELD,
    "ivg.eigenvector": YIELD,
    "ivg.leiden": "YIELD node, community RETURN node, community",
    "ivg.triangleCount": "YIELD node, triangles RETURN node, triangles",
    "ivg.scc": "YIELD node, component RETURN node, component",
    "ivg.kcore": "YIELD node, coreness RETURN node, coreness",
}


def _translate(query, params=None):
    """The translated SQL text."""
    sql = translate_to_sql(parse_query(query), params or {}).sql
    return sql if isinstance(sql, str) else "\n".join(sql)


def _map_call(proc, options):
    return f"CALL {proc}({{{options}}}) {MAP_PROCEDURES[proc]}"


#: Each of these is a value the method (the model in _validate.py) refuses: (procedure, options, the option named)
REFUSED_OPTIONS = [
    ("ivg.closeness", "formula: 'classic'", "formula"),
    ("ivg.closeness", "formula: 'bogus'", "formula"),
    ("ivg.closeness", "formula: 1", "formula"),
    ("ivg.closeness", "formula: 'Classical'", "formula"),
    ("ivg.closeness", "maxHops: -1", "maxHops"),
    ("ivg.closeness", "maxHops: 1001", "maxHops"),
    ("ivg.closeness", "maxHops: 1.5", "maxHops"),
    ("ivg.closeness", "topK: -3", "topK"),
    ("ivg.closeness", "topK: 2.5", "topK"),
    ("ivg.closeness", "topK: 'x'", "topK"),
    ("ivg.closeness", "topK: null", "topK"),
    ("ivg.closeness", "topK: 1000001", "topK"),
    ("ivg.degreeCentrality", "predicate: 5", "predicate"),
    ("ivg.degreeCentrality", "topK: -1", "topK"),
    ("ivg.degreeCentrality", "topK: 'many'", "topK"),
    ("ivg.degreeCentrality", "direction: 1", "direction"),
    ("ivg.betweenness", "sampleSize: -1", "sampleSize"),
    ("ivg.betweenness", "sampleSize: 'all'", "sampleSize"),
    ("ivg.betweenness", "maxHops: -2", "maxHops"),
    ("ivg.betweenness", "maxHops: 1001", "maxHops"),
    ("ivg.betweenness", "topK: -1", "topK"),
    ("ivg.betweenness", "memBudgetMB: 4", "memBudgetMB"),
    ("ivg.betweenness", "memBudgetMB: 9000", "memBudgetMB"),
    ("ivg.eigenvector", "maxIter: 0", "maxIter"),
    ("ivg.eigenvector", "maxIter: 1001", "maxIter"),
    ("ivg.eigenvector", "tol: 0", "tol"),
    ("ivg.eigenvector", "tol: 1.5", "tol"),
    ("ivg.eigenvector", "tol: 'small'", "tol"),
    ("ivg.eigenvector", "topK: -1", "topK"),
    ("ivg.leiden", "maxLevels: 0", "maxLevels"),
    ("ivg.leiden", "maxLevels: 101", "maxLevels"),
    ("ivg.leiden", "gamma: 0", "gamma"),
    ("ivg.leiden", "gamma: 101", "gamma"),
    ("ivg.leiden", "tol: 2", "tol"),
    ("ivg.leiden", "topK: -1", "topK"),
    ("ivg.leiden", "memBudgetMB: 10", "memBudgetMB"),
    ("ivg.leiden", "randomSeed: -1", "randomSeed"),
    ("ivg.leiden", "randomSeed: 'abc'", "randomSeed"),
    ("ivg.triangleCount", "topK: -1", "topK"),
    ("ivg.triangleCount", "topK: 'x'", "topK"),
    ("ivg.scc", "topK: -1", "topK"),
    ("ivg.scc", "topK: 1.5", "topK"),
    ("ivg.kcore", "topK: -1", "topK"),
    ("ivg.kcore", "topK: 1000001", "topK"),
]


@pytest.mark.parametrize("proc,options,option", REFUSED_OPTIONS)
def test_a_procedure_refuses_an_option_value_the_method_refuses(proc, options, option):
    with pytest.raises(ValueError) as caught:
        _translate(_map_call(proc, options))
    message = str(caught.value)
    assert proc in message and option in message, message


def test_a_refused_value_is_in_the_message_so_the_caller_sees_what_was_read():
    with pytest.raises(ValueError, match=r"ivg\.closeness.*formula.*'harmonic' or 'classical'.*'classic'"):
        _translate(_map_call("ivg.closeness", "formula: 'classic'"))


@pytest.mark.parametrize("proc", sorted(MAP_PROCEDURES))
def test_an_unknown_option_key_is_refused_naming_the_key(proc):
    with pytest.raises(ValueError, match=r"Unknown parameters for .*'sideways'"):
        _translate(_map_call(proc, "sideways: 1"))


@pytest.mark.parametrize("proc", sorted(MAP_PROCEDURES))
def test_a_positional_argument_is_refused_where_the_options_are_a_map(proc):
    """``CALL ivg.closeness('classical')`` read no argument at all and answered the harmonic scores."""
    with pytest.raises(ValueError, match=r"takes one options map"):
        _translate(f"CALL {proc}('classical') {MAP_PROCEDURES[proc]}")


ACCEPTED = [
    ("ivg.closeness", "formula: 'harmonic', direction: 'in', maxHops: 3, topK: 5"),
    ("ivg.closeness", "formula: 'classical', direction: 'both', maxHops: 0, topK: 0"),
    ("ivg.closeness", ""),
    ("ivg.degreeCentrality", "direction: 'both', predicate: 'CITES', topK: 0"),
    ("ivg.betweenness", "sampleSize: 0, direction: 'in', maxHops: 1000, topK: 1000000, memBudgetMB: 16"),
    ("ivg.eigenvector", "maxIter: 1000, tol: 0.5, topK: 7"),
    ("ivg.leiden", "maxLevels: 100, gamma: 100, tol: 0.5, topK: 3, memBudgetMB: 8192, randomSeed: 0"),
    ("ivg.leiden", "randomSeed: null"),
    ("ivg.triangleCount", "topK: 10"),
    ("ivg.scc", "topK: 10"),
    ("ivg.kcore", "topK: 10"),
]


@pytest.mark.parametrize("proc,options", ACCEPTED)
def test_a_procedure_still_translates_every_value_the_method_accepts(proc, options):
    sql = _translate(_map_call(proc, options))
    assert "JSON_TABLE" in sql


def test_the_values_a_procedure_reads_are_the_ones_it_was_given():
    """The model's values are what reach the server's function: classical, in, 3 hops, the top 5."""
    sql = _translate(_map_call("ivg.closeness", "formula: 'classical', direction: 'in', maxHops: 3, topK: 5"))
    assert "kg_Closeness('classical', 'in', 3, 5)" in sql


def test_a_parameter_holding_an_option_is_read_and_checked_like_a_literal():
    ok = _translate(_map_call("ivg.closeness", "formula: $f"), {"f": "classical"})
    assert "kg_Closeness('classical', 'out', 0, 10000)" in ok
    with pytest.raises(ValueError, match=r"ivg\.closeness.*formula"):
        _translate(_map_call("ivg.closeness", "formula: $f"), {"f": "classic"})
    with pytest.raises(ValueError, match=r"option formula: parameter '\$f' not found"):
        _translate(_map_call("ivg.closeness", "formula: $f"), {})


# ivg.bm25.search / ivg.ivf.search: BM25SearchInput and VectorSearchInput, the models bm25_search and ivf_search run

@pytest.mark.parametrize("query,option", [
    ("CALL ivg.bm25.search('idx', 'q', 0) YIELD node, score RETURN node", "k"),
    ("CALL ivg.bm25.search('idx', 'q', -4) YIELD node, score RETURN node", "k"),
    ("CALL ivg.bm25.search('', 'q', 3) YIELD node, score RETURN node", "name"),
    ("CALL ivg.bm25.search('idx', '', 3) YIELD node, score RETURN node", "query"),
    ("CALL ivg.bm25.search('idx', '   ', 3) YIELD node, score RETURN node", "query"),
    ("CALL ivg.ivf.search('idx', [1.0, 2.0], 0, 8) YIELD node, score RETURN node", "k"),
    ("CALL ivg.ivf.search('idx', [1.0, 2.0], 3, 0) YIELD node, score RETURN node", "nprobe"),
    ("CALL ivg.ivf.search('idx', [], 3, 2) YIELD node, score RETURN node", "query"),
    ("CALL ivg.ivf.search('', [1.0], 3, 2) YIELD node, score RETURN node", "name"),
])
def test_an_index_search_refuses_the_arguments_its_method_refuses(query, option):
    proc = "ivg.bm25.search" if "bm25" in query else "ivg.ivf.search"
    with pytest.raises(ValueError) as caught:
        _translate(query)
    message = str(caught.value)
    assert proc in message and option in message, message


def test_an_index_search_with_good_arguments_translates():
    sql = _translate("CALL ivg.bm25.search('idx', 'q', 3) YIELD node, score RETURN node")
    assert "kg_BM25('idx', 'q', 3)" in sql
    sql = _translate("CALL ivg.ivf.search('idx', [1.0, 2.0], 3, 2) YIELD node, score RETURN node")
    assert "3, 2)" in sql


# ivg.retrieve, ivg.neighbors, ivg.ppr: no model, but a value of the wrong type is named, where Python's was not

@pytest.mark.parametrize("query,message", [
    ("CALL ivg.retrieve('q', 0) YIELD node RETURN node", r"ivg\.retrieve.*limit"),
    ("CALL ivg.retrieve('q', -3) YIELD node RETURN node", r"ivg\.retrieve.*limit"),
    ("CALL ivg.retrieve('q', 'ten') YIELD node RETURN node", r"ivg\.retrieve.*limit"),
    ("CALL ivg.neighbors(['a'], 5) YIELD neighbor RETURN neighbor", r"ivg\.neighbors.*predicate"),
    ("CALL ivg.ppr(['a'], 'x', 3) YIELD node, score RETURN node", r"ivg\.ppr.*alpha"),
    ("CALL ivg.ppr(['a'], 0.85, 'x') YIELD node, score RETURN node", r"ivg\.ppr: maxIter must be"),
    ("CALL ivg.ppr(['a'], 0.85, 2.5) YIELD node, score RETURN node", r"ivg\.ppr: maxIter must be"),
])
def test_a_procedure_argument_of_the_wrong_type_is_named(query, message):
    with pytest.raises(ValueError, match=message):
        _translate(query)


# gds.pageRank.stream / gds.betweenness.stream: the configuration values, read through int() and float() before

@pytest.fixture
def gds_engine():
    from unittest.mock import MagicMock, patch

    from iris_vector_graph import IRISGraphEngine

    e = IRISGraphEngine(MagicMock(), embedding_dimension=8, native_cypher=False)
    with patch.object(IRISGraphEngine, "kg_PAGERANK", return_value=[("a", 1.0)]) as pr, patch.object(
        IRISGraphEngine, "betweenness_centrality", return_value=[{"id": "a", "score": 1.0}]
    ) as bc:
        e.pr, e.bc = pr, bc
        yield e


@pytest.mark.parametrize("config,option", [
    ("sampleSize: -1", "sampleSize"),
    ("sampleSize: 2.7", "sampleSize"),
    ("sampleSize: 'all'", "sampleSize"),
    ("direction: 'sideways'", "direction"),
    ("direction: 1", "direction"),
    ("maxHops: -1", "maxHops"),
    ("maxHops: 'x'", "maxHops"),
    ("topK: -1", "topK"),
    ("topK: 1.5", "topK"),
])
def test_gds_betweenness_refuses_a_configuration_value_ivg_betweenness_refuses(gds_engine, config, option):
    """``sampleSize: 2.7`` was truncated to 2 by ``int()``; ``'all'`` raised Python's "invalid literal"."""
    with pytest.raises(ValueError, match=rf"gds\.betweenness\.stream.*{option}"):
        gds_engine.execute_cypher(
            f"CALL gds.betweenness.stream('g', {{{config}}}) YIELD nodeId, score", raise_on_error=True)
    gds_engine.bc.assert_not_called()


@pytest.mark.parametrize("config,option", [
    ("dampingFactor: 'high'", "dampingFactor"),
    ("dampingFactor: null", "dampingFactor"),
    ("maxIterations: 2.5", "maxIterations"),
    ("maxIterations: 'many'", "maxIterations"),
    ("maxIterations: null", "maxIterations"),
])
def test_gds_pagerank_names_a_configuration_value_of_the_wrong_type(gds_engine, config, option):
    with pytest.raises(ValueError, match=rf"gds\.pageRank\.stream.*{option}"):
        gds_engine.execute_cypher(
            f"CALL gds.pageRank.stream('g', {{{config}}}) YIELD nodeId, score", raise_on_error=True)
    gds_engine.pr.assert_not_called()


def test_gds_configuration_values_the_methods_accept_still_reach_them(gds_engine):
    gds_engine.execute_cypher(
        "CALL gds.betweenness.stream('g', {sampleSize: 3, direction: 'in', maxHops: 2, topK: 0}) YIELD nodeId, score",
        raise_on_error=True)
    gds_engine.bc.assert_called_once_with(sample_size=3, direction="in", max_hops=2, top_k=0)
    gds_engine.execute_cypher(
        "CALL gds.pageRank.stream('g', {dampingFactor: 0.5, maxIterations: 7}) YIELD nodeId, score",
        raise_on_error=True)
    gds_engine.pr.assert_called_once_with(damping=0.5, max_iterations=7)


# ivg.shortestPath.weighted: maxCost and maxHops

@pytest.mark.parametrize("arguments,label", [
    ("'a', 'b', 'weight', 'far', 10", "maxCost"),
    ("'a', 'b', 'weight', null, 10", "maxCost"),
    ("'a', 'b', 'weight', 9999, 'many'", "maxHops"),
    ("'a', 'b', 'weight', 9999, 2.5", "maxHops"),
    ("'a', 'b', 'weight', 9999, -1", "maxHops"),
])
def test_the_weighted_path_names_a_cost_or_hop_bound_of_the_wrong_type(arguments, label):
    from unittest.mock import MagicMock, patch

    from iris_vector_graph import IRISGraphEngine
    from iris_vector_graph import engine as engine_module

    eng = IRISGraphEngine(MagicMock(), embedding_dimension=4)
    called = []
    with patch.object(engine_module, "_call_classmethod", lambda *a: called.append(a) or "{}"):
        with pytest.raises(ValueError, match=rf"ivg\.shortestPath\.weighted: {label}"):
            eng.execute_cypher(f"CALL ivg.shortestPath.weighted({arguments}) YIELD path, totalCost", {},
                               raise_on_error=True)
    assert called == []
