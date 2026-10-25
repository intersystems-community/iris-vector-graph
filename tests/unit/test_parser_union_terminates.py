"""A UNION branch must parse or raise; it must never loop.

``_parse_union_branch`` looped on any token ``parse_query_part`` does not
consume (``WITH``, ``USE``, ``ORDER``, ``)``...): the body consumed
nothing and appended an empty ``QueryPart`` to a list, growing to about
8 GB in under two minutes.  That is a denial of service for every caller
that parses untrusted Cypher (the Bolt server, the REST API).

Every query here is parsed in a child process under a watchdog that kills
it above 500 MB or after 5 s (``_parse_watchdog``).  Never call the parser
on one of these in the test process: a regression would take the machine
with it, and macOS ignores ``RLIMIT_AS``.
"""

from __future__ import annotations

import pytest

from tests.unit._parse_watchdog import (
    run_guarded_parse,
    self_test_alloc,
    self_test_spin,
)


def _parse(query: str) -> dict:
    r = run_guarded_parse(query)
    assert not r.killed, (
        f"{r.status} (peak {r.peak_rss_mb:.0f} MB, {r.elapsed_s:.1f} s): "
        f"parser did not terminate on {query!r}"
    )
    assert r.stdout.strip(), f"child produced no output: {r.stderr[-500:]}"
    return r.payload()


class TestWatchdogItself:
    """The harness has to kill a runaway before it can protect anything."""

    def test_kills_runaway_allocation(self):
        r = self_test_alloc()
        assert r.status == "KILLED: memory"
        assert r.elapsed_s < 5

    def test_kills_runaway_loop_on_time(self):
        r = self_test_spin(time_limit_s=1.0)
        assert r.status == "KILLED: time"

    def test_lets_a_normal_parse_finish(self):
        r = run_guarded_parse("MATCH (a) RETURN a.id AS i")
        assert r.status == "done"
        assert r.payload()["outcome"] == "ok"


# Queries that hung before the fix. A parse error is an acceptable outcome for
# the malformed ones; the valid ones are pinned to an AST below.
HANGING = [
    # the reported defect: WITH inside a later branch
    "MATCH (a) RETURN a.id AS i UNION MATCH (b) WITH b RETURN b.id AS i",
    "MATCH (a) RETURN a.id AS i UNION ALL MATCH (b) WITH b RETURN b.id AS i",
    # smallest: the branch opens with WITH
    "MATCH (a) RETURN a.id AS i UNION WITH 1 AS x RETURN x AS i",
    # USE GRAPH inside a later branch
    "MATCH (a) RETURN a.id AS i UNION ALL USE GRAPH 'g' MATCH (b) RETURN b.id AS i",
    "MATCH (a) RETURN a.id AS i UNION USE GRAPH g MATCH (b) RETURN b.id AS i",
    # a preceding USE GRAPH does not matter
    "USE GRAPH 'g' MATCH (a) RETURN a.id AS i UNION MATCH (b) WITH b RETURN b.id AS i",
    # WITH in both branches
    "MATCH (a) WITH a RETURN a.id AS i UNION MATCH (b) WITH b RETURN b.id AS i",
    # three branches, the last with a WITH
    "MATCH (a) RETURN a.id AS i UNION MATCH (b) RETURN b.id AS i "
    "UNION MATCH (c) WITH c RETURN c.id AS i",
    # any token the branch loop does not consume
    "MATCH (a) RETURN a.id AS i UNION MATCH (b) ORDER BY b",
    "MATCH (a) RETURN a.id AS i UNION MATCH (b) LIMIT 2",
    "MATCH (a) RETURN a.id AS i UNION MATCH (b) SKIP 1",
    "MATCH (a) RETURN a.id AS i UNION MATCH (b) )",
    "MATCH (a) RETURN a.id AS i UNION MATCH (b) RETURN b.id AS i ) UNION",
    "MATCH (a) RETURN a.id AS i UNION ,",
    "MATCH (a) RETURN a.id AS i UNION 42",
    "MATCH (a) RETURN a.id AS i UNION ALL ALL",
]


@pytest.mark.parametrize("query", HANGING)
def test_union_branch_terminates(query):
    out = _parse(query)
    assert out["outcome"] in ("ok", "error")


MALFORMED = [
    "MATCH (a) RETURN a.id AS i UNION",
    "MATCH (a) RETURN a.id AS i UNION ALL",
    "MATCH (a) RETURN a.id AS i UNION MATCH (b) ORDER BY b",
    "MATCH (a) RETURN a.id AS i UNION MATCH (b) )",
    "MATCH (a) RETURN a.id AS i UNION ,",
    "MATCH (a) RETURN a.id AS i UNION 42",
    "MATCH (a) RETURN a.id AS i UNION ALL ALL",
    "MATCH (a) RETURN a.id AS i UNION MATCH (b) LIMIT 2",
    "MATCH (a) RETURN a.id AS i UNION MATCH (b) SKIP 1",
]


@pytest.mark.parametrize("query", MALFORMED)
def test_malformed_union_is_a_parse_error(query):
    """Input the grammar cannot consume is a clear CypherParseError."""
    out = _parse(query)
    assert out["outcome"] == "error", out
    assert out["type"] == "CypherParseError", out


class TestUnionBranchAst:
    """What a valid union with a WITH inside a branch parses to."""

    def test_with_in_second_branch(self):
        out = _parse(
            "MATCH (a) RETURN a.id AS i UNION MATCH (b) WITH b RETURN b.id AS i"
        )
        assert out["outcome"] == "ok"
        (branch,) = out["unions"]
        assert branch["all"] is False
        assert branch["has_return"] is True
        # MATCH part carrying the WITH, then the (empty) part after it
        assert (branch["parts"], branch["withs"]) == (2, 1)
        assert out["main"]["withs"] == 0

    def test_union_all_flag_is_kept(self):
        out = _parse(
            "MATCH (a) RETURN a.id AS i UNION ALL MATCH (b) WITH b RETURN b.id AS i"
        )
        (branch,) = out["unions"]
        assert branch["all"] is True
        assert (branch["parts"], branch["withs"]) == (2, 1)

    def test_branch_that_opens_with_with(self):
        out = _parse("MATCH (a) RETURN a.id AS i UNION WITH 1 AS x RETURN x AS i")
        assert out["outcome"] == "ok"
        (branch,) = out["unions"]
        assert branch["has_return"] is True
        assert branch["withs"] == 1

    def test_with_in_both_branches(self):
        out = _parse(
            "MATCH (a) WITH a RETURN a.id AS i UNION MATCH (b) WITH b RETURN b.id AS i"
        )
        assert out["main"]["withs"] == 1
        (branch,) = out["unions"]
        assert branch["withs"] == 1

    def test_two_withs_in_one_branch(self):
        out = _parse(
            "MATCH (a) RETURN a.id AS i UNION "
            "MATCH (b) WITH b AS c WITH c AS d RETURN d.id AS i"
        )
        (branch,) = out["unions"]
        assert branch["withs"] == 2

    def test_three_branches_mixed(self):
        out = _parse(
            "MATCH (a) RETURN a.id AS i UNION MATCH (b) RETURN b.id AS i "
            "UNION MATCH (c) WITH c RETURN c.id AS i"
        )
        first, second = out["unions"]
        assert (first["withs"], second["withs"]) == (0, 1)
        assert second["has_return"] is True

    def test_branch_ast_matches_the_same_query_standalone(self):
        """A branch parses exactly like the query would on its own."""
        body = "MATCH (b) WITH b WHERE b.x > 1 RETURN b.id AS i LIMIT 4"
        standalone = _parse(body)["main"]
        branch = _parse(f"MATCH (a) RETURN a.id AS i UNION {body}")["unions"][0]
        for key in ("parts", "withs", "has_return", "skip", "limit"):
            assert branch[key] == standalone[key], key

    def test_limit_after_last_branch_is_the_branchs(self):
        out = _parse(
            "MATCH (a) RETURN a.id AS i UNION MATCH (b) WITH b RETURN b.id AS i LIMIT 3"
        )
        (branch,) = out["unions"]
        assert branch["limit"] is not None

    def test_plain_union_unchanged(self):
        out = _parse("MATCH (a) RETURN a.id AS i UNION MATCH (b) RETURN b.id AS i")
        (branch,) = out["unions"]
        assert (branch["parts"], branch["withs"], branch["has_return"]) == (1, 0, True)

    def test_return_only_branch_unchanged(self):
        out = _parse("MATCH (a) RETURN a.id AS i UNION RETURN 1 AS i")
        (branch,) = out["unions"]
        assert (branch["parts"], branch["has_return"]) == (0, True)


class TestUseGraphInsideABranch:
    """``USE GRAPH`` is a prefix of the whole query, not of a branch.

    The translator rebuilds every branch without a graph context, so a
    branch-level ``USE GRAPH`` that parsed would scope nothing and read as
    if it did. It is a syntax error that says so.
    """

    @pytest.mark.parametrize(
        "query",
        [
            "MATCH (a) RETURN a.id AS i UNION USE GRAPH 'g' MATCH (b) RETURN b.id AS i",
            "MATCH (a) RETURN a.id AS i UNION ALL USE GRAPH g MATCH (b) RETURN b.id AS i",
        ],
    )
    def test_is_a_clear_error(self, query):
        out = _parse(query)
        assert out["outcome"] == "error", out
        assert out["type"] == "CypherParseError"
        assert "USE GRAPH" in out["message"]

    def test_leading_use_graph_still_parses(self):
        out = _parse(
            "USE GRAPH 'g' MATCH (a) RETURN a.id AS i UNION MATCH (b) RETURN b.id AS i"
        )
        assert out["outcome"] == "ok"
        assert out["main"]["graph"] == "g"
