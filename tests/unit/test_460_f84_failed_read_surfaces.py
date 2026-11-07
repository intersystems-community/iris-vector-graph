"""4.6.0 fixes84: a failed Cypher read is an error on GraphQL and Bolt, as it is on execute_cypher and REST.

execute_cypher answers a failed read with `result.error` set and no rows (2d1d6f46); REST raises it (`_run_cypher`).
The GraphQL resolver and the Bolt server never read it, so a read IRIS refused, or one that raised in an ObjectScript
algorithm (ivg.eigenvector's PowerIterationFailedConvergence on a graph with no cycle, found by fixes83's E2E), answered
no rows and no error there: a silent wrong answer.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from iris_vector_graph.result import IVGResult

ERROR = ("<SQL ERROR>; Details: [SQLCODE: <-149>] [%msg: <SQL Function GRAPH_KG.KG_EIGENVECTOR failed with error: "
         "SQLCODE=-400,%msg=ERROR:PowerIterationFailedConvergence: eigenvector centrality did not converge>]")


class _Engine:
    def __init__(self, result):
        self.result, self.queries = result, []

    def execute_cypher(self, query, parameters=None, **kw):
        self.queries.append(query)
        return self.result


def _failed():
    return IVGResult(columns=[], rows=[], error=ERROR)


def test_graphql_resolve_cypher_raises_a_failed_read():
    from iris_vector_graph.gql.resolvers import resolve_cypher

    with pytest.raises(Exception, match="PowerIterationFailedConvergence"):
        asyncio.run(resolve_cypher(SimpleNamespace(context={"engine": _Engine(_failed())}), "CALL ivg.eigenvector()"))


def test_graphql_answers_a_read_that_did_not_fail():
    from iris_vector_graph.gql.resolvers import resolve_cypher

    ok = IVGResult(columns=["k"], rows=[[1]])
    out = asyncio.run(resolve_cypher(SimpleNamespace(context={"engine": _Engine(ok)}), "RETURN 1 AS k"))
    assert (out.columns, out.rows) == (["k"], [[1]])


def _bolt_run(result):
    from iris_vector_graph.bolt_server import BoltSession

    sent = []
    session = BoltSession.__new__(BoltSession)
    session._get_engine = lambda: _Engine(result)
    session._pending_result = None

    async def send(tag, fields):
        sent.append((tag, fields))

    session._send_message = send
    asyncio.run(session._handle_run("CALL ivg.eigenvector()", {}, {}))
    return session, sent


def test_bolt_sends_failure_for_a_failed_read():
    from iris_vector_graph.bolt_server import TAG_FAILURE, TAG_SUCCESS, BoltState

    session, sent = _bolt_run(_failed())
    assert [t for t, _ in sent] == [TAG_FAILURE], sent
    assert "PowerIterationFailedConvergence" in sent[0][1]["message"]
    assert session.state == BoltState.FAILED and session._pending_result is None
    assert TAG_SUCCESS not in [t for t, _ in sent]


def test_bolt_answers_a_read_that_did_not_fail():
    from iris_vector_graph.bolt_server import TAG_SUCCESS

    _, sent = _bolt_run(IVGResult(columns=["k"], rows=[[1]]))
    assert [t for t, _ in sent] == [TAG_SUCCESS] and sent[0][1]["fields"] == ["k"], sent


def test_the_api_apps_stats_raise_a_failed_read():
    """api/'s GraphQL app (the development server, api/main.py) caught every exception in `stats` and answered zero
    nodes, zero edges; a failed count answered 0 too. Both are the field's error now."""
    from api.gql.core.resolvers import CoreQuery

    info = SimpleNamespace(context={"engine": _Engine(_failed())})
    with pytest.raises(Exception, match="PowerIterationFailedConvergence"):
        asyncio.run(CoreQuery.stats(CoreQuery(), info))
