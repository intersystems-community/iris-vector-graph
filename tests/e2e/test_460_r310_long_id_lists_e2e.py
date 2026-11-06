"""4.6.0 R310, live: the APIs that take or reach many node ids answer for over 500 of them (fixes64).

A Cypher DETACH DELETE over 500 nodes failed `-202` because it inlined 1000 UUID literals (~40,000 characters, more
than IRIS prepares). Each API below that reaches many ids gets one case of the same size, so a chunker that inlines
too much shows up. The graphs, by hand: 1200 nodes `r310b:<i>` (label L310B), in a chain of 1199 `NEXT` relationships,
or a hub `r310b:hub` with 1200 `SPOKE` relationships out.
"""

from __future__ import annotations

import os

import pytest

from iris_vector_graph.engine import IRISGraphEngine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = [pytest.mark.e2e, pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")]

N = 1200
IDS = [f"r310b:{i}" for i in range(N)]


def _count(e, q):
    r = e.execute_cypher(q, raise_on_error=True)
    return r.rows[0][0]


@pytest.fixture()
def eng(iris_connection):
    e = IRISGraphEngine(iris_connection)
    e.execute_cypher("MATCH (n:L310B) DETACH DELETE n", raise_on_error=True)
    try:
        yield e
    finally:
        e.execute_cypher("MATCH (n:L310B) DETACH DELETE n")


@pytest.fixture()
def chain(eng):
    eng.bulk_create_nodes([{"id": nid, "labels": ["L310B"], "properties": {"i": i}} for i, nid in enumerate(IDS)])
    eng.bulk_create_edges(
        [{"source_id": IDS[i], "predicate": "NEXT", "target_id": IDS[i + 1]} for i in range(N - 1)]
    )
    assert _count(eng, "MATCH (n:L310B) RETURN count(n)") == N
    assert _count(eng, "MATCH (:L310B)-[r:NEXT]->() RETURN count(r)") == N - 1
    return eng


def test_bulk_delete_nodes(chain):
    chain.bulk_delete_nodes(IDS, graph=None)
    assert _count(chain, "MATCH (n:L310B) RETURN count(n)") == 0
    assert _count(chain, "MATCH ()-[r:NEXT]->() RETURN count(r)") == 0


def test_delete_nodes_by_prefix(chain):
    assert chain.delete_nodes_by_prefix("r310b:", graph=None) == N
    assert _count(chain, "MATCH (n:L310B) RETURN count(n)") == 0
    assert _count(chain, "MATCH ()-[r:NEXT]->() RETURN count(r)") == 0


def test_cypher_delete_of_many_relationships(chain):
    chain.execute_cypher("MATCH (:L310B)-[r:NEXT]->() DELETE r", raise_on_error=True)
    assert _count(chain, "MATCH ()-[r:NEXT]->() RETURN count(r)") == 0
    assert _count(chain, "MATCH (n:L310B) RETURN count(n)") == N


def test_cypher_set_and_remove_on_many_nodes(chain):
    chain.execute_cypher("MATCH (n:L310B) SET n.flag = 1", raise_on_error=True)
    assert _count(chain, "MATCH (n:L310B) WHERE n.flag = 1 RETURN count(n)") == N
    chain.execute_cypher("MATCH (n:L310B) REMOVE n.flag", raise_on_error=True)
    assert _count(chain, "MATCH (n:L310B) WHERE n.flag IS NULL RETURN count(n)") == N


def test_delete_node_of_a_hub(eng):
    eng.bulk_create_nodes([{"id": nid, "labels": ["L310B"]} for nid in IDS + ["r310b:hub"]])
    eng.bulk_create_edges([{"source_id": "r310b:hub", "predicate": "SPOKE", "target_id": nid} for nid in IDS])
    assert _count(eng, "MATCH (:L310B {node_id: 'r310b:hub'})-[r:SPOKE]->() RETURN count(r)") == N
    assert eng.delete_node("r310b:hub", graph=None) is True
    assert _count(eng, "MATCH ()-[r:SPOKE]->() RETURN count(r)") == 0
    assert _count(eng, "MATCH (n:L310B) RETURN count(n)") == N


def test_embed_nodes_over_many_ids(chain):
    width = chain._get_embedding_dimension()
    asked = []

    class Fake:
        def encode(self, texts, **_):
            import numpy as np

            if isinstance(texts, str):
                asked.append(texts)
                return np.full(width, 0.5)
            asked.extend(texts)
            return np.full((len(texts), width), 0.5)

    chain.embed_nodes(model=Fake(), text_fn=lambda nid, props: nid, node_ids=IDS, force=True, graph=None)
    assert sorted(asked) == sorted(IDS)


def test_embed_nodes_over_many_ids_given_twice_embeds_each_once(chain):
    width = chain._get_embedding_dimension()
    asked = []

    class Fake:
        def encode(self, texts, **_):
            import numpy as np

            if isinstance(texts, str):
                asked.append(texts)
                return np.full(width, 0.5)
            asked.extend(texts)
            return np.full((len(texts), width), 0.5)

    # 10 ids repeated after their first place, so each repeat falls in a later run of 500
    given = IDS + IDS[:10]
    chain.embed_nodes(model=Fake(), text_fn=lambda nid, props: nid, node_ids=given, force=True, graph=None)
    assert sorted(asked) == sorted(IDS)
