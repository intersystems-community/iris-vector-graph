"""Spec 227 US1 gate — a KNN returns only the searched graph's neighbours.

Phase 3's gate, against the live enterprise container. The guarantee is in the
column declarations and the statement text, so a mock cannot observe it: a
MagicMock cursor happily "returns" whatever the test hands it, which is how a
scope leak stays green for a year.

Both graphs write the *same* width, from the same (unnamed) model, and that is
deliberate. Two widths is the routing gate's question
(`tests/e2e/test_227_two_models.py`); what this gate proves is narrower and more
important: two graphs whose vectors are as alike as they can be — same width,
same `model_key`, so the route differs by graph alone — and a search of A still
cannot see B.

Covers spec US1 scenarios 1–4 (FR-003, FR-004, FR-032, SC-001).
"""

import pytest

pytestmark = [pytest.mark.e2e]

#: 5 per graph, so `k=10` asks for more than one graph holds. A leak shows up as
#: a row count above 5 rather than as a wrong ID, which is the cheaper assertion
#: to read when it fails.
PER_GRAPH = 5


def _ids(graph_tag: str) -> list:
    return [f"ivg227:knn:{graph_tag}:{i}" for i in range(PER_GRAPH)]


def _vector(env, fill: float) -> list:
    """A constant vector at the width this fixture's routes are created with.

    From the fixture, not from `engine.embedding_dimension`: `ivg227_env` builds its
    engine without a declared dimension (that attribute is `None`, and `int(None)`
    raised in every test here), and after routing the width belongs to the route
    rather than to the engine. Each `(graph, model)` pair gets its own table created
    at the width of its first write, so declaring it here is what makes both graphs
    the same width on purpose instead of by accident.
    """
    return [fill] * env.dim_a


@pytest.fixture
def populated(ivg227_env):
    """5 nodes and 5 vectors in each of graphs A and B."""
    env = ivg227_env
    eng = env.engine
    for i, node_id in enumerate(_ids("a")):
        eng.create_node(node_id, labels=["Patient"], graph=env.graph_a)
        eng.store_embedding(node_id, _vector(env, 0.10 + i / 100), graph=env.graph_a)
    for i, node_id in enumerate(_ids("b")):
        eng.create_node(node_id, labels=["Member"], graph=env.graph_b)
        eng.store_embedding(node_id, _vector(env, 0.90 - i / 100), graph=env.graph_b)
    yield env
    # `ivg227_env` wipes both graphs afterwards, routed tables included. Deleting
    # these node IDs out of `kg_NodeEmbeddings` by hand is what this teardown used to
    # do, and it would now be a no-op that reads like cleanup: the vectors are in
    # `kg_emb_<hash>` tables the wipe drops.


def test_k_larger_than_the_graph_returns_only_that_graphs_rows(populated):
    """Scenario 1. `k=10` over 5+5: at most 5 rows, every one of them A's.

    Asking for more than the graph holds is the shape that catches a missing
    predicate, because a correct answer has to be *short*. A KNN that quietly
    tops up from graph B returns a full 10 rows and looks healthier than the
    correct one.
    """
    env = populated
    eng = env.engine
    a_ids = set(_ids("a"))

    rows = eng.kg_KNN_VEC(env.as_query(_vector(env, 0.1)), k=10, graph=env.graph_a)

    assert len(rows) <= PER_GRAPH, (
        f"asked for 10 from a graph holding {PER_GRAPH}; got {len(rows)} rows, so "
        f"the search crossed graphs: {rows}"
    )
    returned = {r[0] for r in rows}
    assert returned <= a_ids, f"rows from outside graph A: {returned - a_ids}"
    assert returned, "graph A returned nothing at all — the predicate matches no row"


def test_switching_the_graph_returns_the_other_graphs_rows(populated):
    """Scenario 4. B's rows for B, and the two result sets are disjoint.

    Documented as expected, not as a leak: spec 227 is collision avoidance, not
    authorization (out of scope §"Out of scope"). A caller who can call the method
    can name any graph. What must not happen is a caller *seeing both at once*.
    """
    env = populated
    eng = env.engine
    query = env.as_query(_vector(env, 0.9))

    from_a = {r[0] for r in eng.kg_KNN_VEC(query, k=10, graph=env.graph_a)}
    from_b = {r[0] for r in eng.kg_KNN_VEC(query, k=10, graph=env.graph_b)}

    assert from_b <= set(_ids("b")), f"graph B returned foreign rows: {from_b}"
    assert not (from_a & from_b), f"the same row came back from both graphs: {from_a & from_b}"


def test_an_omitted_graph_reaches_only_the_default_graph(populated):
    """Scenario 2. No `graph=` means the default graph, never every graph.

    Neither fixture graph is the default graph, so the correct answer here is
    empty. An unscoped scan returns A's and B's rows and would pass any test that
    only checked "results are plausible".
    """
    env = populated
    eng = env.engine
    query = env.as_query(_vector(env, 0.1))

    # `kg_KNN_VEC` answers `[]` for anything it cannot do, so an empty result on its
    # own proves nothing here. The same query scoped to A must find A's rows first.
    assert eng.kg_KNN_VEC(query, k=10, graph=env.graph_a), (
        "the scoped control found nothing, so the empty unscoped answer below would "
        "be about a broken search rather than about scope"
    )

    # The default graph is a route like any other, and its table's width is whatever
    # the install declared — not this fixture's. A 384-wide query against a 768-wide
    # default table is refused before it reads anything (`_assert_query_width`), which
    # is the right answer to a wrong-width query and no answer at all about scope.
    default_table, default_route = eng._route_for_read("", None)
    default_width = getattr(default_route, "dimension", None) or env.dim_a
    rows = eng.kg_KNN_VEC(env.as_query([0.1] * int(default_width)), k=10)

    foreign = {r[0] for r in rows} & (set(_ids("a")) | set(_ids("b")))
    assert not foreign, (
        f"an omitted graph reached named graphs: {foreign}. `graph=None` is the "
        f"default graph; there is no value meaning every graph (FR-004)"
    )


def test_the_client_side_fallback_has_the_same_scope(populated):
    """Scenario 3. The fallback is the path nobody exercises until something else
    broke — a partially upgraded install, a missing procedure. If it widens, the
    failure mode of an upgrade is a cross-graph answer with no error anywhere."""
    env = populated
    eng = env.engine
    query = env.as_query(_vector(env, 0.1))

    scoped = {r[0] for r in eng.kg_KNN_VEC(query, k=10, graph=env.graph_a)}
    fallback = eng._kg_KNN_VEC_client_side(query, 10, None, graph=env.graph_a)
    fallback_ids = {r[0] for r in fallback}

    assert fallback_ids <= set(_ids("a")), (
        f"the client-side fallback crossed graphs: {fallback_ids - set(_ids('a'))}"
    )
    assert fallback_ids == scoped, (
        f"the fallback and the procedure disagree on scope: "
        f"only-procedure={scoped - fallback_ids}, only-fallback={fallback_ids - scoped}"
    )
