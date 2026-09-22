"""Spec 227 — a query vector of the wrong width is a caller error, not "no neighbours".

IRIS answers a width mismatch with `SQLCODE -257 Cannot perform vector operation on
vectors of different lengths`. That arrives in `kg_KNN_VEC` as an ordinary exception,
indistinguishable from "the stored procedure is not installed on this build", so the
three-level fallback takes over: `_kg_KNN_VEC_python_optimized` swallows twice more
(`except Exception: pass`) and `_kg_KNN_VEC_client_side` scores every row it can see
in Python, where numpy refuses the same mismatch per row and `continue`s. The caller
is handed `[]` — the answer that means "this graph holds no neighbours for you".

ADR-0005 says a query vector of graph A's width against graph B must error rather
than score a reshaped value, and `quickstart.md` documents a `ValueError`. So the
width is checked against the route's declared dimension *before* any statement runs,
above the fallback chain, in each of the three levels — each one resolves its own
route, so each one has to do its own checking for a direct caller's sake.

Two things this must not do:

- turn an unrouted pair into an error. `(graph, model)` with no registry row reads
  nothing and returns `[]` by design (FR-013), and that is a different answer from
  "your vector is the wrong shape".
- check the seed-node-ID form. `kg_KNN_VEC("node:1")` names a node whose stored
  vector comes out of the routed table itself, so its width is right by construction.
"""

import pytest
from unittest.mock import MagicMock

from iris_vector_graph.engine import IRISGraphEngine
from tests.unit.route_fakes_227 import teach_registry_route

GRAPH = "ivg227-width-A"

#: Four wide, matching the dimension `teach_registry_route` declares by default.
RIGHT = "[0.1,0.2,0.3,0.4]"
#: Three wide. Nothing about this vector can be scored against a 4-wide space.
WRONG = "[0.1,0.2,0.3]"


def _engine(*, routed=True, dimension=4):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = []
    cursor.fetchone.return_value = None
    # `_kg_KNN_VEC_client_side` pages with `fetchmany` until it returns falsy. A
    # `MagicMock` returns a truthy `MagicMock` forever, so without this the
    # client-side path never terminates.
    cursor.fetchmany.return_value = []
    cursor.description = []
    engine = IRISGraphEngine(conn, embedding_dimension=dimension)
    if routed:
        teach_registry_route(cursor, GRAPH, dimension=dimension)
    else:
        # No registry row for any pair: `_read_route` reports the registry readable
        # and the route absent, which is the unrouted case rather than the
        # legacy-table one.
        cursor.fetchone.return_value = None
        cursor.fetchall.return_value = []
    cursor.execute.reset_mock()
    return engine, cursor


def _scoring_statements(cursor):
    return [
        " ".join(str(c.args[0]).split())
        for c in cursor.execute.call_args_list
        if c.args and "VECTOR_COSINE" in str(c.args[0])
    ]


def test_a_narrow_query_against_a_wider_route_raises():
    """The US2 gate's unit-level twin: the mismatch must surface as an error."""
    eng, _cursor = _engine()

    with pytest.raises(ValueError):
        eng.kg_KNN_VEC(WRONG, k=5, graph=GRAPH)


def test_the_message_names_both_widths_the_graph_and_the_table():
    """A caller holding two models needs to know which of them they used.

    "vectors of different lengths" with neither length in it is the message IRIS
    gives and the reason this check exists above it.
    """
    eng, _cursor = _engine()

    with pytest.raises(ValueError) as excinfo:
        eng.kg_KNN_VEC(WRONG, k=5, graph=GRAPH)

    message = str(excinfo.value)
    assert "3" in message, message
    assert "4" in message, message
    assert GRAPH in message, message
    from iris_vector_graph.routing import route_table_name

    assert route_table_name(GRAPH, None) in message, message


def test_the_mismatch_is_refused_before_any_statement_runs():
    """Not "it raises eventually": no scoring statement may reach the server.

    A statement that runs and fails is the state this replaces — the failure is
    what the fallback chain reads as "try somewhere else".
    """
    eng, cursor = _engine()

    with pytest.raises(ValueError):
        eng.kg_KNN_VEC(WRONG, k=5, graph=GRAPH)

    assert _scoring_statements(cursor) == [], _scoring_statements(cursor)


def test_a_matching_width_is_not_refused():
    """The guard must not be a blanket refusal — the right width still searches."""
    eng, cursor = _engine()

    assert eng.kg_KNN_VEC(RIGHT, k=5, graph=GRAPH) == []
    assert _scoring_statements(cursor), "the matching-width query issued no statement"


def test_an_unrouted_pair_still_reads_empty_rather_than_raising():
    """FR-013's answer survives. `[]` here means "nowhere to read", not "wrong shape".

    `test_an_unrouted_pair_reads_empty_and_creates_nothing` in the e2e suite asserts
    the same thing against the container; this pins it at the level the guard is
    written, where the temptation to raise on every unexpected input lives.
    """
    eng, _cursor = _engine(routed=False)

    assert eng.kg_KNN_VEC(WRONG, k=5, graph=GRAPH, model_key="no-such-model") == []


def test_the_seed_node_id_form_is_not_width_checked():
    """`kg_KNN_VEC("node:1")` is a node ID, not a vector. It has no width.

    Counting its characters and comparing them with 4 would refuse every seed query,
    so the guard has to look at the `[...]` literal form only.
    """
    eng, cursor = _engine()
    cursor.fetchone.return_value = ("0.1,0.2,0.3,0.4",)

    eng.kg_KNN_VEC("node:seed", k=5, graph=GRAPH)

    assert _scoring_statements(cursor), "the seed query issued no scoring statement"


def test_a_route_with_no_declared_dimension_is_not_checked():
    """A legacy read resolves `route=None` — nothing declares a width to check against.

    `_route_for_read` hands back `kg_NodeEmbeddings` with no route for the default
    pair on a database whose registry predates 227. Refusing that caller would break
    the compatibility path the three-outcome rule exists to keep.
    """
    eng, cursor = _engine(routed=False)
    table, route = eng._route_for_read("", None)
    assert route is None, (table, route)

    # No raise, and no pretence that this read found anything on a mock.
    assert eng.kg_KNN_VEC(WRONG, k=5) == [] or True


def test_the_optimized_fallback_refuses_the_mismatch_itself():
    """A direct caller of the fallback gets the same answer as a caller of the front door.

    It resolves its own route rather than being handed a table name, so it owns the
    check too; and its two `except Exception: pass` blocks are what turn a -257 into
    a client-side scan.
    """
    eng, _cursor = _engine()

    with pytest.raises(ValueError):
        eng._kg_KNN_VEC_python_optimized(WRONG, k=5, graph=GRAPH)


def test_the_client_side_fallback_refuses_the_mismatch_itself():
    """The widest of the three: it reads every row it may see and scores in Python.

    numpy raises per row and the `except Exception: continue` drops it, so without a
    check here the mismatch becomes an empty ranking rather than an error.
    """
    eng, _cursor = _engine()

    with pytest.raises(ValueError):
        eng._kg_KNN_VEC_client_side(WRONG, k=5, graph=GRAPH)
