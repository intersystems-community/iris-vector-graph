"""Spec 230 US2 gate — every retrieval leg answers from one graph.

Spec 227 scoped the node-vector leg and left the rest of retrieval namespace-wide.
Four legs were still graph-blind, and each of them is proven here against IRIS
because each guarantee is in a storage layout or a server-side predicate:

* **edge vectors** (SC-004, FR-006) — `Graph_KG.kg_EdgeEmbeddings` was one table
  keyed `(s, p, o_id)` at one declared width, so two graphs could not embed their
  edges under different models and `edge_vector_search` answered every graph's
  edges. Routing it per `(graph, model)` is the fix, and IRIS is the only place it
  can be checked: two widths under one triple can only work in two tables with two
  declarations (SQLCODE -104 at INSERT otherwise).
* **fused search** (SC-005, FR-008) — `kg_RRF_FUSE` handed `:graphId` to the
  vector leg only, and joined node IDs to document IDs so the `FULL OUTER JOIN`
  could never match. Both halves are server-side SQL.
* **the BM25 leg** (FR-009) — `Graph.KG.BM25Index.Build` read `Graph_KG.nodes`
  with no graph predicate at all, into a global keyed by index name.
* **the IVF leg** (FR-009) — scoped by 227; guarded here because it is the one leg
  a refactor of the others can silently re-widen.

`SKIP_IRIS_TESTS=true` fails rather than skips: a skipped scope test is
indistinguishable from a passing one, which is how 27 tests stayed fake-green.
"""

from __future__ import annotations

import contextlib
import os

import pytest

pytestmark = [pytest.mark.e2e]

GRAPH_A = "ivg230:retr:a"
GRAPH_B = "ivg230:retr:b"

MODEL_A = "ivg230-retr-model-a"
MODEL_B = "ivg230-retr-model-b"

DIM_A = 8
DIM_B = 16

#: In both graphs. The point of the fixture: one entity, two graphs, two models.
SHARED = "ivg230:retr:shared"
#: In both graphs, as the target of the shared edge.
TARGET = "ivg230:retr:target"
#: In graph B only. Every "did it leak" assertion is an assertion about this ID:
#: a shared ID cannot distinguish a scoped answer from an unscoped one.
ONLY_B = "ivg230:retr:only-b"

PRED = "IVG230_RETR_LINKS"

#: A token that appears in no other row in the namespace, so the text leg's
#: candidate set is exactly this fixture's documents.
TERM = "ivg230zebraword"

GRAPHS = (GRAPH_A, GRAPH_B)


def _vec(dim: int, fill: float) -> list:
    return [float(fill)] * dim


def _as_query(vector) -> str:
    return "[" + ",".join(str(float(v)) for v in vector) + "]"


def _routed_tables(conn, graph: str) -> list:
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT table_name FROM Graph_KG.embedding_registry "
            "WHERE COALESCE(graph_id, '') = COALESCE(?, '')",
            (graph,),
        )
        return [row[0] for row in cursor.fetchall()]
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


def _wipe(conn):
    """Remove both graphs, their routed tables and their documents.

    Routed tables are dropped, not emptied: a route is created on demand at a
    declared width, so one left over from an earlier run answers this run's route
    lookup with a stale width and the width assertions pass for the wrong reason.
    """
    cursor = conn.cursor()
    try:
        for graph in GRAPHS:
            for table in _routed_tables(conn, graph):
                with contextlib.suppress(Exception):
                    cursor.execute(f"DROP TABLE Graph_KG.{table}")
            with contextlib.suppress(Exception):
                cursor.execute(
                    "DELETE FROM Graph_KG.embedding_registry "
                    "WHERE COALESCE(graph_id, '') = ?",
                    (graph,),
                )
            for table in ("rdf_edges", "rdf_props", "rdf_labels", "docs"):
                with contextlib.suppress(Exception):
                    cursor.execute(
                        f"DELETE FROM Graph_KG.{table} "
                        "WHERE COALESCE(graph_id, '') = ?",
                        (graph,),
                    )
            with contextlib.suppress(Exception):
                cursor.execute(
                    "DELETE FROM Graph_KG.kg_NodeEmbeddings "
                    "WHERE COALESCE(graph_id, '') = ?",
                    (graph,),
                )
            with contextlib.suppress(Exception):
                cursor.execute(
                    "DELETE FROM Graph_KG.kg_EdgeEmbeddings "
                    "WHERE s IN (?, ?)",
                    (SHARED, ONLY_B),
                )
            with contextlib.suppress(Exception):
                cursor.execute(
                    "DELETE FROM Graph_KG.nodes WHERE COALESCE(graph_id, '') = ?",
                    (graph,),
                )
        with contextlib.suppress(Exception):
            conn.commit()
    finally:
        with contextlib.suppress(Exception):
            cursor.close()


def _require_iris(iris_connection):
    if os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true":
        pytest.fail(
            "spec 230 US2 asserts server-side retrieval scope and a storage layout. "
            "SKIP_IRIS_TESTS=true is not an acceptable outcome — start "
            "ivg-iris-enterprise with scripts/enterprise-container.sh up."
        )
    if iris_connection is None:
        pytest.fail(
            "no live IRIS connection: a graph predicate inside a stored procedure "
            "cannot be observed from Python."
        )


@pytest.fixture
def edge_env(iris_connection):
    """One edge in both graphs, plus a second edge only graph B has.

    Vectors are written through the engine, not by direct INSERT: the route has to
    be created by the write (FR-006 follows 227's FR-013), and a test that created
    the tables itself would pass against an engine that never routes anything.
    """
    _require_iris(iris_connection)

    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    _wipe(iris_connection)

    for graph in GRAPHS:
        engine.create_node(SHARED, labels=["Ivg230Shared"], graph=graph)
        engine.create_node(TARGET, labels=["Ivg230Target"], graph=graph)
        engine.create_edge(SHARED, PRED, TARGET, graph=graph)
    engine.create_node(ONLY_B, labels=["Ivg230OnlyB"], graph=GRAPH_B)
    engine.create_edge(SHARED, PRED, ONLY_B, graph=GRAPH_B)

    engine.store_edge_embedding(
        SHARED, PRED, TARGET, _vec(DIM_A, 0.1), graph=GRAPH_A, model_key=MODEL_A
    )
    engine.store_edge_embedding(
        SHARED, PRED, TARGET, _vec(DIM_B, 0.2), graph=GRAPH_B, model_key=MODEL_B
    )
    engine.store_edge_embedding(
        SHARED, PRED, ONLY_B, _vec(DIM_B, 0.3), graph=GRAPH_B, model_key=MODEL_B
    )

    yield engine
    _wipe(iris_connection)


# --- T027 / SC-004: edge vectors per graph and per model -------------------------


def test_edge_routes_are_distinct_per_graph_and_model(edge_env):
    engine = edge_env

    route_a = engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A, kind="edge")
    route_b = engine.resolve_route(graph=GRAPH_B, model_key=MODEL_B, kind="edge")

    assert route_a is not None, "graph A's edge write created no route"
    assert route_b is not None, "graph B's edge write created no route"
    assert route_a.table_name != route_b.table_name, (
        "both graphs' edge vectors routed to one table, which cannot hold two "
        "declared widths (SQLCODE -104 at INSERT)"
    )


def test_an_edge_route_is_never_the_node_route_for_the_same_pair(edge_env):
    """One table cannot hold both: node rows are keyed `(graph_id, node_id)` with an
    FK to `nodes`, edge rows `(graph_id, s, p, o_id)`."""
    engine = edge_env

    node_route = engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A, kind="node")
    edge_route = engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A, kind="edge")

    assert edge_route is not None
    if node_route is not None:
        assert node_route.table_name != edge_route.table_name


def test_each_edge_route_declares_its_own_width(edge_env):
    """The declaration is what IRIS enforces, so the dictionary is the witness —
    not the registry row the same code path wrote."""
    from iris_vector_graph.schema import GraphSchema

    engine = edge_env
    route_a = engine.resolve_route(graph=GRAPH_A, model_key=MODEL_A, kind="edge")
    route_b = engine.resolve_route(graph=GRAPH_B, model_key=MODEL_B, kind="edge")

    cursor = engine.conn.cursor()
    try:
        declared_a = GraphSchema.get_embedding_dimension(
            cursor, f"Graph_KG.{route_a.table_name}"
        )
        declared_b = GraphSchema.get_embedding_dimension(
            cursor, f"Graph_KG.{route_b.table_name}"
        )
    finally:
        with contextlib.suppress(Exception):
            cursor.close()

    assert declared_a == DIM_A, f"graph A's edge route declares {declared_a}"
    assert declared_b == DIM_B, f"graph B's edge route declares {declared_b}"


def test_each_graphs_edge_search_returns_only_its_own_edges(edge_env):
    """SC-004. `ONLY_B` is the discriminator: graph A has no such edge, so a row
    naming it in graph A's answer came from graph B's table."""
    engine = edge_env

    from_a = engine.edge_vector_search(
        _vec(DIM_A, 0.1), top_k=50, graph=GRAPH_A, model_key=MODEL_A
    )
    from_b = engine.edge_vector_search(
        _vec(DIM_B, 0.2), top_k=50, graph=GRAPH_B, model_key=MODEL_B
    )

    assert from_a, "graph A's edge search found nothing it wrote"
    assert from_b, "graph B's edge search found nothing it wrote"

    targets_a = {row["o_id"] for row in from_a}
    targets_b = {row["o_id"] for row in from_b}

    assert ONLY_B not in targets_a, (
        f"graph A's edge search returned graph B's edge: {sorted(targets_a)}"
    )
    assert targets_a == {TARGET}, sorted(targets_a)
    assert targets_b == {TARGET, ONLY_B}, sorted(targets_b)


def test_an_edge_query_of_the_wrong_width_is_refused(edge_env):
    """ADR-0005, restated for edges: a padded or truncated query still produces a
    number, and a number is what a caller will use."""
    engine = edge_env

    with pytest.raises(Exception):
        engine.edge_vector_search(
            _vec(DIM_B, 0.2), top_k=5, graph=GRAPH_A, model_key=MODEL_A
        )


def test_a_pair_with_no_edge_route_answers_nothing(edge_env):
    """Not the default table, and not another model's: a substituted route returns
    real vectors from the wrong space, which score and rank like an answer."""
    engine = edge_env

    rows = engine.edge_vector_search(
        _vec(DIM_A, 0.1), top_k=5, graph=GRAPH_A, model_key="ivg230-retr-model-absent"
    )

    assert rows == [], rows


# --- T028 / SC-005: fused search is scoped, and fuses ---------------------------


@pytest.fixture
def fused_env(iris_connection):
    """Vectors in `kg_NodeEmbeddings` and text in `docs`, for two graphs.

    `kg_RRF_FUSE` is a server-side procedure over the default route — its `FROM` is
    fixed at `kg_NodeEmbeddings` — so the rows are inserted there directly, under
    an explicit `graph_id`. That is the state a migrated multi-graph install is in,
    and the graph predicate inside the procedure is what this fixture exists to
    test. Going through `store_embedding` would route a named graph's vectors into
    a hashed table the procedure never reads.
    """
    _require_iris(iris_connection)

    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    _wipe(iris_connection)

    dimension = _default_route_width(iris_connection)

    for graph in GRAPHS:
        engine.create_node(SHARED, labels=["Ivg230Shared"], graph=graph)
    engine.create_node(ONLY_B, labels=["Ivg230OnlyB"], graph=GRAPH_B)

    cursor = iris_connection.cursor()
    try:
        rows = (
            (GRAPH_A, SHARED, 0.11),
            (GRAPH_B, SHARED, 0.12),
            (GRAPH_B, ONLY_B, 0.13),
        )
        for graph, node_id, fill in rows:
            cursor.execute(
                "INSERT INTO Graph_KG.kg_NodeEmbeddings (graph_id, node_id, emb) "
                "VALUES (?, ?, TO_VECTOR(?, DOUBLE, ?))",
                (graph, node_id, _as_query(_vec(dimension, fill)), dimension),
            )
            cursor.execute(
                "INSERT INTO Graph_KG.docs (graph_id, id, text) VALUES (?, ?, ?)",
                (graph, node_id, f"{TERM} {TERM} document for {node_id}"),
            )
        iris_connection.commit()
    finally:
        with contextlib.suppress(Exception):
            cursor.close()

    yield engine, dimension
    _wipe(iris_connection)


def _default_route_width(conn) -> int:
    """The width `kg_NodeEmbeddings.emb` is declared at in this namespace."""
    from iris_vector_graph.schema import GraphSchema

    cursor = conn.cursor()
    try:
        width = GraphSchema.get_embedding_dimension(
            cursor, "Graph_KG.kg_NodeEmbeddings"
        )
    finally:
        with contextlib.suppress(Exception):
            cursor.close()
    assert width, "kg_NodeEmbeddings declares no vector width"
    return int(width)


def _fuse(conn, *, query_vector: str, query_text: str, graph: str, k: int = 25):
    """Call the server-side `kg_RRF_FUSE` and return its rows.

    Both invocation forms are attempted because a procedure with a result set is
    reachable either way depending on how IRIS classified it, and a test that
    silently returned `[]` for "this build spells it the other way" would read as a
    scoped answer. Neither form working is a failure with both messages shown.
    """
    attempts = (
        "SELECT * FROM Graph_KG.kg_RRF_FUSE(?, ?, ?, ?, ?, ?, ?)",
        "CALL Graph_KG.kg_RRF_FUSE(?, ?, ?, ?, ?, ?, ?)",
    )
    params = (k, 100, 100, 60, query_vector, query_text, graph)
    errors = []
    for sql in attempts:
        cursor = conn.cursor()
        try:
            cursor.execute(sql, params)
            return [tuple(row) for row in cursor.fetchall()]
        except Exception as exc:  # noqa: BLE001 - reported below if all forms fail
            errors.append(f"{sql} -> {exc}")
        finally:
            with contextlib.suppress(Exception):
                cursor.close()
    pytest.fail("kg_RRF_FUSE could not be called:\n" + "\n".join(errors))


def test_fused_search_is_scoped(fused_env):
    """SC-005, first half: no row in graph A's fusion came from graph B."""
    engine, dimension = fused_env

    rows = _fuse(
        engine.conn,
        query_vector=_as_query(_vec(dimension, 0.11)),
        query_text=TERM,
        graph=GRAPH_A,
    )

    ids = {row[0] for row in rows}
    assert ids, "a scoped fusion over a populated graph returned nothing"
    assert ONLY_B not in ids, (
        f"graph A's fusion returned graph B's document: {sorted(ids)}"
    )
    assert ids == {SHARED}, sorted(ids)


def test_fused_search_actually_fuses(fused_env):
    """SC-005, second half.

    Before the re-key the vector leg answered node IDs and the text leg answered
    document IDs, so the `FULL OUTER JOIN` matched nothing and every "fused" row
    carried one leg's score and NULL for the other. One row with both is the whole
    claim.
    """
    engine, dimension = fused_env

    rows = _fuse(
        engine.conn,
        query_vector=_as_query(_vec(dimension, 0.11)),
        query_text=TERM,
        graph=GRAPH_A,
    )

    both = [row for row in rows if row[2] is not None and row[3] is not None]
    assert both, (
        "no result row carries a score from both legs, so the join still never "
        f"matches: {rows}"
    )


def test_the_fusion_still_answers_four_columns(fused_env):
    """`(id, rrf, vs, ts)` is unpacked positionally by callers."""
    engine, dimension = fused_env

    rows = _fuse(
        engine.conn,
        query_vector=_as_query(_vec(dimension, 0.11)),
        query_text=TERM,
        graph=GRAPH_A,
    )

    assert rows, "nothing to check the shape of"
    assert all(len(row) == 4 for row in rows), rows


def test_the_text_leg_alone_is_scoped(fused_env):
    """`kg_TXT` is reachable on its own, and a caller that only wants text must not
    be handed every graph's documents."""
    engine, _dimension = fused_env

    cursor = engine.conn.cursor()
    try:
        cursor.execute(
            "SELECT * FROM Graph_KG.kg_TXT(?, ?, ?, ?)", (TERM, 50, 0, GRAPH_A)
        )
        rows = [tuple(row) for row in cursor.fetchall()]
    finally:
        with contextlib.suppress(Exception):
            cursor.close()

    ids = {row[0] for row in rows}
    assert ids == {SHARED}, (
        f"kg_TXT answered outside the graph it was given: {sorted(ids)}"
    )


# --- T029 / FR-009: the BM25 and IVF legs -------------------------------------


@pytest.fixture
def hybrid_env(iris_connection):
    """Two graphs whose nodes carry the same text property and their own vectors."""
    _require_iris(iris_connection)

    from iris_vector_graph import IRISGraphEngine

    engine = IRISGraphEngine(iris_connection)
    _wipe(iris_connection)

    for graph in GRAPHS:
        engine.create_node(
            SHARED,
            labels=["Ivg230Shared"],
            properties={"name": f"{TERM} shared entity"},
            graph=graph,
        )
    engine.create_node(
        ONLY_B,
        labels=["Ivg230OnlyB"],
        properties={"name": f"{TERM} graph b only"},
        graph=GRAPH_B,
    )

    engine.store_embedding(
        SHARED, _vec(DIM_A, 0.1), graph=GRAPH_A, model_key=MODEL_A
    )
    engine.store_embedding(
        SHARED, _vec(DIM_A, 0.2), graph=GRAPH_B, model_key=MODEL_A
    )
    engine.store_embedding(
        ONLY_B, _vec(DIM_A, 0.3), graph=GRAPH_B, model_key=MODEL_A
    )

    index_a = "ivg230retra"
    index_b = "ivg230retrb"
    yield engine, index_a, index_b

    for name in (index_a, index_b):
        with contextlib.suppress(Exception):
            engine.bm25_drop(name)
        with contextlib.suppress(Exception):
            engine.ivf_drop(name)
    _wipe(iris_connection)


def test_bm25_leg_is_scoped(hybrid_env):
    """FR-009. `Graph.KG.BM25Index.Build` read `Graph_KG.nodes` with no graph
    predicate, so one index held both graphs and the same node ID in two graphs
    merged into one document."""
    engine, index_a, _index_b = hybrid_env

    engine.bm25_build(index_a, ["name"], graph=GRAPH_A)
    hits = engine.bm25_search(index_a, TERM, k=50)

    ids = {node_id for node_id, _score in hits}
    assert ids, "a BM25 index built over a populated graph found nothing"
    assert ONLY_B not in ids, (
        f"the BM25 leg returned a node from another graph: {sorted(ids)}"
    )
    assert ids == {SHARED}, sorted(ids)


def test_two_bm25_indexes_over_two_graphs_stay_separate(hybrid_env):
    """Each index is built for one graph, and the second build must not adopt the
    first's documents — the global is keyed by index name, so a missing graph in
    its config would let the names collide on content."""
    engine, index_a, index_b = hybrid_env

    engine.bm25_build(index_a, ["name"], graph=GRAPH_A)
    engine.bm25_build(index_b, ["name"], graph=GRAPH_B)

    ids_a = {node_id for node_id, _ in engine.bm25_search(index_a, TERM, k=50)}
    ids_b = {node_id for node_id, _ in engine.bm25_search(index_b, TERM, k=50)}

    assert ids_a == {SHARED}, sorted(ids_a)
    assert ids_b == {SHARED, ONLY_B}, sorted(ids_b)


def test_ivf_leg_is_scoped(hybrid_env):
    """Scoped by 227; guarded because it is the leg a refactor of the others can
    silently re-widen."""
    engine, index_a, _index_b = hybrid_env

    engine.ivf_build(index_a, nlist=2, graph=GRAPH_A, model_key=MODEL_A)
    hits = engine.ivf_search(index_a, _vec(DIM_A, 0.1), k=50)

    ids = {node_id for node_id, _score in hits}
    assert ids, "an IVF index built over a populated graph found nothing"
    assert ONLY_B not in ids, (
        f"the IVF leg returned a node from another graph: {sorted(ids)}"
    )
    assert ids == {SHARED}, sorted(ids)


# --- T040 / Principle VII: the translator's SQL over the re-keyed `docs` -------
#
# `docs` was rebuilt under the translator (FR-007), and `ivg.retrieve` is the one
# procedure whose fusion reads it. Translating is not the gate — the SQL executing on
# the server is, because every failure this re-key can cause (a dropped index, a
# column that moved, a key that is now composite) is a Prepare or Query Open error
# and none of it is visible from a translation unit test.


RETRIEVE_INDEX = "ivg230retrdocs"


@pytest.fixture
def retrieve_env(fused_env):
    """`fused_env`, plus what `CALL ivg.retrieve(...)` needs to reach the server.

    Two additions, both so the call fails for real reasons only:

    * a BM25 index for graph A. The fusion's text arm names one, and an index that
      does not exist is an error from the arm rather than an answer from the graph.
    * a deterministic embedder. The vector arm embeds the query text, and this
      namespace has no `%Embedding.Config` — the enterprise build cannot import
      `sentence_transformers` — so the fallback would either fail or answer at a
      width the default route does not declare (SQLCODE -104). A stub at the
      declared width keeps the test about the re-key.
    """
    engine, dimension = fused_env

    engine.embedder = lambda _text: _vec(dimension, 0.11)
    engine.bm25_build(RETRIEVE_INDEX, ["name"], graph=GRAPH_A)

    yield engine, dimension

    with contextlib.suppress(Exception):
        engine.bm25_drop(RETRIEVE_INDEX)


def _nodes(result) -> set:
    return {row[0] for row in result.rows}


def test_retrieve_executes_over_the_rekeyed_docs(retrieve_env):
    """The Principle VII gate: the fusion's SQL still runs on the server.

    `docs` is a rebuilt table now — new primary key, new column order, indexes
    recreated by the migration — and this is the statement that reads it.
    """
    engine, _dimension = retrieve_env

    result = engine.execute_cypher(
        f"USE GRAPH '{GRAPH_A}' "
        f"CALL ivg.retrieve('{TERM}', 5, '{RETRIEVE_INDEX}', '*', 60) "
        "YIELD node, rrf_score RETURN node, rrf_score"
    )

    ids = _nodes(result)
    assert ids, "the fusion executed but answered nothing over a populated graph"
    assert ONLY_B not in ids, f"the fusion crossed into graph B: {sorted(ids)}"
    assert ids == {SHARED}, sorted(ids)


def test_a_label_filtered_retrieve_still_joins(retrieve_env):
    """A non-wildcard label sends the vector arm through `rdf_labels`, which carries
    its own graph predicate — a second place the scope has to hold."""
    engine, _dimension = retrieve_env

    result = engine.execute_cypher(
        f"USE GRAPH '{GRAPH_A}' "
        f"CALL ivg.retrieve('{TERM}', 5, '{RETRIEVE_INDEX}', 'Ivg230Shared', 60) "
        "YIELD node, rrf_score RETURN node, rrf_score"
    )

    assert _nodes(result) == {SHARED}, result.rows


def test_a_label_filtered_match_answers_one_graph(retrieve_env):
    """The other half of T040. `MATCH (n:Label)` reads `rdf_labels` joined to
    `nodes`, whose unique constraint is now `(graph_id, node_id)` — the same ID in
    two graphs is two nodes, and a label-filtered read must see one of them."""
    engine, _dimension = retrieve_env

    mine = engine.execute_cypher(
        f"USE GRAPH '{GRAPH_A}' MATCH (n:Ivg230Shared) RETURN n.id AS id"
    )
    theirs = engine.execute_cypher(
        f"USE GRAPH '{GRAPH_A}' MATCH (n:Ivg230OnlyB) RETURN n.id AS id"
    )

    assert _nodes(mine) == {SHARED}, mine.rows
    assert theirs.rows == [], (
        f"graph A matched a label only graph B asserts: {theirs.rows}"
    )
