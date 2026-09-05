"""Spec 213 US6 — enumerate history and retrieve a revision (live ivg-iris-enterprise). T063."""

import os
import time

import pytest

from iris_vector_graph.ledger import Changeset, UnknownRevisionError
from tests.integration._ledger_helpers import make_engine

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"
pytestmark = pytest.mark.skipif(SKIP_IRIS_TESTS, reason="SKIP_IRIS_TESTS=true")


@pytest.fixture
def engine(iris_connection, ledger_reset):
    eng = make_engine(iris_connection)
    eng.ledger.enable()
    return eng


def _five_commits(engine):
    actors = [
        ("agent:planner-7", "agent"),
        ("human:tom", "human"),
        ("agent:planner-7", "agent"),
        ("ingest:etl", "ingest"),
        ("human:ana", "human"),
    ]
    out = []
    for i, (actor, kind) in enumerate(actors):
        cs = Changeset(actor=actor, actor_type=kind, message=f"m{i}")
        cs.create_node(f"h{i}", labels=["L"], properties={"i": str(i)})
        out.append(engine.ledger.commit(cs).revision)
        time.sleep(0.002)
    return out


class TestUS6History:
    def test_forward_order_contiguous_and_deterministic(self, engine):
        revs = _five_commits(engine)
        page = engine.ledger.history(limit=100)
        seqs = [r.seq for r in page.revisions]
        assert seqs == list(range(1, 7))
        assert page.revisions[0].kind == "genesis"
        assert [r.revision_id for r in page.revisions[1:]] == [r.revision_id for r in revs]
        again = engine.ledger.history(limit=100)
        assert [r.__dict__ for r in again.revisions] == [r.__dict__ for r in page.revisions]
        assert page.next_after_seq is None

    def test_paging_covers_every_revision_exactly_once(self, engine):
        _five_commits(engine)
        seen = []
        after = 0
        while True:
            page = engine.ledger.history(after_seq=after, limit=2)
            seen.extend(r.seq for r in page.revisions)
            if page.next_after_seq is None:
                break
            after = page.next_after_seq
        assert seen == list(range(1, 7))

    def test_filters_preserve_order(self, engine):
        revs = _five_commits(engine)
        by_actor = engine.ledger.history(actor="agent:planner-7").revisions
        assert [r.seq for r in by_actor] == [2, 4]
        by_type = engine.ledger.history(actor_type="human").revisions
        assert [r.seq for r in by_type] == [3, 6]
        mid = revs[2].committed_ms
        rng = engine.ledger.history(since_ms=mid, until_ms=revs[-1].committed_ms).revisions
        assert [r.seq for r in rng] == [4, 5, 6] or [r.seq for r in rng][0] >= 4
        assert [r.seq for r in rng] == sorted(r.seq for r in rng)
        desc = engine.ledger.history(descending=True, limit=3).revisions
        assert [r.seq for r in desc] == [6, 5, 4]

    def test_get_revision_returns_ordered_records_with_prior_and_new(self, engine):
        cs = Changeset(actor="a", actor_type="human")
        cs.create_node("n", labels=["L"], properties={"k": "1"})
        cs.set_property("n", "k", "2")
        cs.create_node("m")
        r = cs.create_relationship("n", "R", "m", qualifiers={"w": "1"})
        cs.set_qualifier(r, "w", "2")
        res = engine.ledger.commit(cs)
        rev = engine.ledger.get_revision(res.revision.revision_id)
        assert rev.info.revision_id == res.revision.revision_id
        assert [rec.ordinal for rec in rev.records] == list(range(1, len(rev.records) + 1))
        ops = [(rec.op, rec.entity_kind, rec.attr, rec.prior, rec.new) for rec in rev.records]
        assert ops[0] == ("create_node", "node", "", None, "")
        assert ("add_label", "node", "L", None, "") in ops
        assert ("set_prop", "node", "k", None, "1") in ops
        assert ("set_prop", "node", "k", "1", "2") in ops
        rel_ops = [o for o in ops if o[1] == "rel"]
        assert (
            rel_ops[0][0] == "create_rel" and rel_ops[0][3] is None and '"s":"n"' in rel_ops[0][4]
        )
        assert ("set_qual", "rel", "w", None, "1") in ops and (
            "set_qual",
            "rel",
            "w",
            "1",
            "2",
        ) in ops
        stmt = res.stmt_ids[3]
        assert all(rec.entity_id == stmt for rec in rev.records if rec.entity_kind == "rel")

    def test_unknown_revision_is_not_found(self, engine):
        with pytest.raises(UnknownRevisionError):
            engine.ledger.get_revision("0" * 32)
        with pytest.raises(UnknownRevisionError):
            engine.ledger.diff("0" * 32, engine.ledger.head().revision_id)
