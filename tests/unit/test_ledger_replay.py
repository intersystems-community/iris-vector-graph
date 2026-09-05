"""Spec 213 — unit tests for iris_vector_graph.ledger.replay (no IRIS required).

T025 TestRecordDecode (US1). Later phases add TestRecordPaging (US6), TestDiff (US7),
TestReconstruct (US8), TestVerify (US9), TestLifecycle (US10), TestTemporalExclusion (US11).
"""

import json
import os

import pytest

from iris_vector_graph.ledger import replay as R

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

TUPLE = json.dumps(
    {"graph": None, "o": "tank-2", "p": "FEEDS", "s": "pump-7"},
    sort_keys=True,
    separators=(",", ":"),
)


def _row(seq, ordinal, op, kind, eid, attr="", prior=None, new=None, graph=None, flags=""):
    """Row shape produced by Graph_KG.ledger_records: prior/new plus *_null flags."""
    return (
        seq,
        ordinal,
        op,
        kind,
        eid,
        attr,
        "" if prior is None else prior,
        1 if prior is None else 0,
        "" if new is None else new,
        1 if new is None else 0,
        "" if graph is None else graph,
        flags,
    )


ALL_OPS = [
    "create_node",
    "delete_node",
    "add_label",
    "remove_label",
    "set_prop",
    "remove_prop",
    "create_rel",
    "delete_rel",
    "set_qual",
    "remove_qual",
]


class TestRecordDecode:
    @pytest.mark.parametrize("op", ALL_OPS)
    def test_from_row_decodes_every_op(self, op):
        rec = R.MutationRecord.from_row(
            _row(3, 1, op, "node", "n1", "k", "a", "b", None, "cascade,genesis")
        )
        assert rec.seq == 3 and rec.ordinal == 1 and rec.op == op
        assert rec.entity_kind == "node" and rec.entity_id == "n1" and rec.attr == "k"
        assert rec.prior == "a" and rec.new == "b" and rec.graph is None
        assert rec.flags == ["cascade", "genesis"]

    def test_null_flags_become_none_and_empty_string_survives(self):
        rec = R.MutationRecord.from_row(_row(1, 1, "set_prop", "node", "n", "k", None, ""))
        assert rec.prior is None and rec.new == ""
        rec2 = R.MutationRecord.from_row(_row(1, 2, "remove_prop", "node", "n", "k", "", None))
        assert rec2.prior == "" and rec2.new is None

    def test_from_wire_roundtrip(self):
        rec = R.MutationRecord.from_row(_row(2, 5, "set_qual", "rel", "7", "w", "1", "2", "g", ""))
        wire = rec.to_wire()
        assert wire["graph"] == "g" and wire["flags"] == []
        assert R.MutationRecord.from_wire(wire) == rec

    def test_graph_state_applies_all_kinds(self):
        st = R.GraphState()
        recs = [
            _row(1, 1, "create_node", "node", "pump-7"),
            _row(1, 2, "create_node", "node", "tank-2"),
            _row(1, 3, "add_label", "node", "pump-7", "Equipment", None, ""),
            _row(1, 4, "set_prop", "node", "pump-7", "status", None, "ok"),
            _row(1, 5, "create_rel", "rel", "1", "", None, TUPLE),
            _row(1, 6, "set_qual", "rel", "1", "weight", None, "1.0"),
            _row(1, 7, "set_qual", "rel", "1", "capacity", None, "100"),
        ]
        for r in recs:
            st.apply(R.MutationRecord.from_row(r))
        assert st.nodes["pump-7"].labels == {"Equipment"}
        assert st.nodes["pump-7"].props == {"status": "ok"}
        s1 = st.statements["1"]
        assert (s1.s, s1.p, s1.o, s1.graph) == ("pump-7", "FEEDS", "tank-2", None)
        assert s1.quals == {"weight": "1.0", "capacity": "100"}
        assert st.entity_count == 3

        more = [
            _row(2, 1, "remove_label", "node", "pump-7", "Equipment", "", None),
            _row(2, 2, "set_prop", "node", "pump-7", "status", "ok", "warn"),
            _row(2, 3, "remove_qual", "rel", "1", "capacity", "100", None),
            _row(2, 4, "remove_prop", "node", "pump-7", "status", "warn", None),
            _row(2, 5, "delete_rel", "rel", "1", "", "{}", None),
            _row(2, 6, "delete_node", "node", "pump-7", "", "{}", None),
        ]
        for r in more:
            st.apply(R.MutationRecord.from_row(r))
        assert "pump-7" not in st.nodes and "1" not in st.statements
        assert st.nodes["tank-2"].labels == set()
        assert st.entity_count == 1

    def test_apply_is_tolerant_of_missing_entities(self):
        st = R.GraphState()
        st.apply(
            R.MutationRecord.from_row(_row(1, 1, "remove_label", "node", "ghost", "L", "", None))
        )
        st.apply(R.MutationRecord.from_row(_row(1, 2, "delete_rel", "rel", "99", "", "{}", None)))
        assert st.entity_count == 0


class TestRecordPaging:
    """US6: iter_records walks (seq, ordinal) order across page boundaries."""

    @staticmethod
    def _rows():
        rows = []
        for seq in (1, 2, 3):
            for ordinal in range(1, 6):
                rows.append(
                    _row(seq, ordinal, "set_prop", "node", f"n{seq}", f"k{ordinal}", None, "v")
                )
        return rows

    def test_pages_are_stitched_in_order(self):
        rows = self._rows()

        def fetch_page(seq, ord_from, page=4):
            start = next(
                (i for i, r in enumerate(rows) if (r[0], r[1]) >= (seq, ord_from)), len(rows)
            )
            return rows[start : start + page]

        got = [(r.seq, r.ordinal) for r in R.iter_records(fetch_page, 1, 3)]
        assert got == [(r[0], r[1]) for r in rows]
        assert len(got) == 15

    def test_seq_to_bound_is_respected(self):
        rows = self._rows()

        def fetch_page(seq, ord_from):
            start = next(
                (i for i, r in enumerate(rows) if (r[0], r[1]) >= (seq, ord_from)), len(rows)
            )
            return rows[start : start + 100]

        got = [(r.seq, r.ordinal) for r in R.iter_records(fetch_page, 2, 2)]
        assert got == [(2, i) for i in range(1, 6)]

    def test_empty_history(self):
        assert list(R.iter_records(lambda s, o: [], 1, 5)) == []


def _rec(seq, ordinal, op, kind, eid, attr="", prior=None, new=None, graph=None, flags=""):
    return R.MutationRecord.from_row(
        _row(seq, ordinal, op, kind, eid, attr, prior, new, graph, flags)
    )


class TestDiff:
    """US7: net, lifecycle-aware, totally ordered diff (FR-027 to FR-029a)."""

    def test_net_collapse_within_one_lifecycle(self):
        recs = [
            _rec(2, 1, "set_prop", "node", "n", "k", None, "1"),
            _rec(3, 1, "set_prop", "node", "n", "k", "1", "2"),
            _rec(4, 1, "remove_prop", "node", "n", "k", "2", None),
        ]
        d = R.compute_diff(recs)
        assert d.to_list() == []  # set then removed → no net change

    def test_net_value_change_reports_first_prior_and_last_new(self):
        recs = [
            _rec(2, 1, "set_prop", "node", "n", "k", "a", "b"),
            _rec(3, 1, "set_prop", "node", "n", "k", "b", "c"),
            _rec(3, 2, "add_label", "node", "n", "L", None, ""),
        ]
        d = R.compute_diff(recs)
        assert d.to_list() == [
            {
                "entity_kind": "node",
                "entity_id": "n",
                "attr": "label:L",
                "before": None,
                "after": "",
            },
            {
                "entity_kind": "node",
                "entity_id": "n",
                "attr": "prop:k",
                "before": "a",
                "after": "c",
            },
        ]

    def test_node_delete_then_recreate_is_lifecycle_aware(self):
        recs = [
            _rec(5, 1, "delete_node", "node", "valve-3", "", '{"labels":["V"],"props":{}}', None),
            _rec(7, 1, "create_node", "node", "valve-3", "", None, ""),
            _rec(7, 2, "add_label", "node", "valve-3", "V", None, ""),
        ]
        d = R.compute_diff(recs)
        kinds = [(e["attr"], e["before"], e["after"]) for e in d.to_list()]
        assert ("", '{"labels":["V"],"props":{}}', None) in kinds  # deletion of first lifecycle
        assert ("", None, "") in kinds  # creation of second lifecycle
        assert ("label:V", None, "") in kinds

    def test_rel_delete_then_recreate_distinct_statement_ids(self):
        recs = [
            _rec(4, 1, "delete_rel", "rel", "10", "", '{"s":"a"}', None),
            _rec(6, 1, "create_rel", "rel", "11", "", None, TUPLE),
            _rec(6, 2, "set_qual", "rel", "11", "w", None, "1"),
        ]
        d = R.compute_diff(recs)
        ids = [
            (e["entity_id"], e["attr"], e["before"] is None, e["after"] is None)
            for e in d.to_list()
        ]
        assert ("10", "", False, True) in ids
        assert ("11", "", True, False) in ids
        assert ("11", "qual:w", True, False) in ids

    def test_created_and_deleted_inside_range_leaves_no_trace(self):
        recs = [
            _rec(2, 1, "create_node", "node", "tmp", "", None, ""),
            _rec(2, 2, "set_prop", "node", "tmp", "k", None, "v"),
            _rec(3, 1, "delete_node", "node", "tmp", "", "{}", None),
        ]
        assert R.compute_diff(recs).to_list() == []

    def test_total_order_and_determinism(self):
        recs = [
            _rec(2, 1, "set_qual", "rel", "9", "z", None, "1"),
            _rec(2, 2, "set_prop", "node", "b", "k", None, "1"),
            _rec(2, 3, "set_prop", "node", "a", "k", None, "1"),
            _rec(2, 4, "add_label", "node", "a", "A", None, ""),
        ]
        d1, d2 = R.compute_diff(recs), R.compute_diff(list(recs))
        assert d1 == d2
        assert [(e["entity_kind"], e["entity_id"], e["attr"]) for e in d1.to_list()] == [
            ("node", "a", "label:A"),
            ("node", "a", "prop:k"),
            ("node", "b", "prop:k"),
            ("rel", "9", "qual:z"),
        ]

    def test_inverse_is_exact_and_involutive(self):
        recs = [
            _rec(2, 1, "create_node", "node", "n", "", None, ""),
            _rec(2, 2, "set_prop", "node", "n", "k", None, "v"),
            _rec(3, 1, "set_prop", "node", "m", "k", "1", "2"),
        ]
        d = R.compute_diff(recs)
        inv = d.inverse()
        assert [(e["before"], e["after"]) for e in inv.to_list()] == [
            (x.after, x.before) for x in sorted(d.entries, key=R._sort_key)
        ]
        assert inv.inverse() == d

    def test_empty_range_is_empty(self):
        assert R.compute_diff([]).to_list() == []


class TestReconstruct:
    """US8: fold to GraphState at a revision, bound/stream, export line shapes."""

    def _recs(self):
        return [
            _rec(1, 1, "create_node", "node", "a"),
            _rec(1, 2, "add_label", "node", "a", "L", None, ""),
            _rec(1, 3, "create_node", "node", "b"),
            _rec(
                2,
                1,
                "create_rel",
                "rel",
                "1",
                "",
                None,
                json.dumps(
                    {"graph": None, "o": "b", "p": "R", "s": "a"},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
            _rec(2, 2, "set_qual", "rel", "1", "w", None, "1"),
            _rec(3, 1, "set_prop", "node", "a", "k", None, "v"),
            _rec(4, 1, "delete_rel", "rel", "1", "", "{}", None),
            _rec(5, 1, "delete_node", "node", "b", "", "{}", None),
        ]

    def test_state_at_intermediate_revision(self):
        recs = [r for r in self._recs() if r.seq <= 3]
        st = R.reconstruct(recs, bound=None)
        assert (
            set(st.nodes) == {"a", "b"}
            and st.nodes["a"].props == {"k": "v"}
            and st.nodes["a"].labels == {"L"}
        )
        assert st.statements["1"].quals == {"w": "1"} and st.statements["1"].tuple == (
            "a",
            "R",
            "b",
            None,
        )
        assert st.stmt_for_tuple(("a", "R", "b", None)) == "1"

    def test_state_at_head_after_deletes(self):
        st = R.reconstruct(self._recs(), bound=None)
        assert set(st.nodes) == {"a"} and st.statements == {}
        assert st.entity_count == 1

    def test_bound_refuses_without_stream_and_yields_with_stream(self):
        from iris_vector_graph.ledger.errors import ReconstructionTooLargeError

        with pytest.raises(ReconstructionTooLargeError) as ei:
            R.reconstruct([r for r in self._recs() if r.seq <= 2], bound=2, stream=False)
        assert ei.value.bound == 2 and ei.value.estimate == 3
        items = list(R.reconstruct([r for r in self._recs() if r.seq <= 2], bound=2, stream=True))
        assert sorted((k, i) for k, i, _ in items) == [("node", "a"), ("node", "b"), ("rel", "1")]

    def test_export_line_shapes(self, tmp_path):
        from iris_vector_graph.ledger.export import export_ndjson

        st = R.reconstruct([r for r in self._recs() if r.seq <= 3], bound=None)
        summary = export_ndjson(st, str(tmp_path / "out.ndjson"))
        assert (summary.nodes, summary.rels) == (2, 1)
        lines = [json.loads(line) for line in open(tmp_path / "out.ndjson")]
        assert lines[0] == {"type": "node", "id": "a", "labels": ["L"], "props": {"k": "v"}}
        rel = [line for line in lines if line["type"] == "rel"][0]
        assert rel == {
            "type": "rel",
            "s": "a",
            "p": "R",
            "o": "b",
            "graph": None,
            "quals": {"w": "1"},
            "stmt_id": "1",
        }


class _FakeCursor:
    def __init__(self, tables):
        self.tables = tables
        self._rows = []

    def execute(self, sql, params=None):
        for key, rows in self.tables.items():
            if f".{key}" in sql:
                self._rows = list(rows)
                return
        self._rows = []

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _FakeConn:
    def __init__(self, tables):
        self.tables = tables

    def cursor(self):
        return _FakeCursor(self.tables)


class TestVerify:
    """US9: read_canonical excludes reserved props; compare classifies differences."""

    def test_read_canonical_excludes_reserved_and_joins_stmt_ids(self):
        conn = _FakeConn(
            {
                "nodes": [("a",), ("b",)],
                "rdf_labels": [("a", "L")],
                "rdf_props": [("a", "k", "v"), ("a", "id", "a"), ("a", "__graph", "g")],
                "rdf_edges": [
                    ("a", "R", "b", None, '{"w": 1, "flag": true}'),
                    ("b", "R", "a", "g", None),
                ],
            }
        )
        st = R.read_canonical(
            conn, "Graph_KG", lambda t: "7" if t == ("a", "R", "b", None) else None
        )
        assert st.nodes["a"].props == {"k": "v"} and st.nodes["a"].labels == {"L"}
        assert st.statements["7"].quals == {"w": "1", "flag": "true"}
        unrec = [k for k in st.statements if k.startswith("tuple:")]
        assert len(unrec) == 1 and st.statements[unrec[0]].graph == "g"

    def test_compare_equal(self):
        a, b = R.GraphState(), R.GraphState()
        for st in (a, b):
            st.apply(_rec(1, 1, "create_node", "node", "n"))
            st.apply(_rec(1, 2, "set_prop", "node", "n", "k", None, "v"))
        entries, cls = R.compare(a, b)
        assert entries == [] and cls is None

    def test_compare_reports_unrecorded_node_and_attrs(self):
        replayed, canonical = R.GraphState(), R.GraphState()
        canonical.apply(_rec(1, 1, "create_node", "node", "extra"))
        canonical.apply(_rec(1, 2, "add_label", "node", "extra", "X", None, ""))
        canonical.apply(_rec(1, 3, "set_prop", "node", "extra", "k", None, "1"))
        entries, cls = R.compare(replayed, canonical)
        assert cls == "unrecorded_writes"
        assert [(e.entity_id, e.attr, e.before, e.after) for e in entries] == [
            ("extra", "", None, ""),
            ("extra", "label:X", None, ""),
            ("extra", "prop:k", None, "1"),
        ]

    def test_compare_reports_missing_statement_and_qual_change(self):
        replayed, canonical = R.GraphState(), R.GraphState()
        for st in (replayed, canonical):
            st.apply(_rec(1, 1, "create_node", "node", "a"))
            st.apply(_rec(1, 2, "create_node", "node", "b"))
        replayed.apply(
            _rec(
                2,
                1,
                "create_rel",
                "rel",
                "5",
                "",
                None,
                TUPLE.replace("tank-2", "b").replace("pump-7", "a").replace("FEEDS", "R"),
            )
        )
        replayed.apply(_rec(2, 2, "set_qual", "rel", "5", "w", None, "1"))
        canonical.apply(
            _rec(
                2,
                1,
                "create_rel",
                "rel",
                "5",
                "",
                None,
                TUPLE.replace("tank-2", "b").replace("pump-7", "a").replace("FEEDS", "R"),
            )
        )
        canonical.apply(_rec(2, 2, "set_qual", "rel", "5", "w", None, "2"))
        entries, cls = R.compare(replayed, canonical)
        assert cls == "unrecorded_writes"
        assert [(e.entity_kind, e.entity_id, e.attr, e.before, e.after) for e in entries] == [
            ("rel", "5", "qual:w", "1", "2")
        ]
        canonical.apply(_rec(3, 1, "delete_rel", "rel", "5", "", "{}", None))
        entries, _ = R.compare(replayed, canonical)
        assert (
            entries[0].entity_kind == "rel"
            and entries[0].entity_id == "5"
            and entries[0].after is None
        )

    def test_adoption_items_roundtrip_shapes(self):
        replayed, canonical = R.GraphState(), R.GraphState()
        canonical.apply(_rec(1, 1, "create_node", "node", "n"))
        canonical.apply(_rec(1, 2, "set_prop", "node", "n", "k", None, "1"))
        canonical.apply(_rec(1, 3, "create_node", "node", "m"))
        canonical.apply(
            _rec(
                1,
                4,
                "create_rel",
                "rel",
                "tuple:x",
                "",
                None,
                json.dumps(
                    {"graph": None, "o": "m", "p": "R", "s": "n"},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        )
        canonical.apply(_rec(1, 5, "set_qual", "rel", "tuple:x", "w", None, "3"))
        entries, _ = R.compare(replayed, canonical)
        items = R._adoption_items(entries, replayed)
        ops = [(i["op"], i["entity_kind"]) for i in items]
        assert ("create_node", "node") in ops and ("set_prop", "node") in ops
        assert ("create_rel", "rel") in ops and ("set_qual", "rel") in ops
        rel = [i for i in items if i["op"] == "create_rel"][0]
        assert json.loads(rel["new"])["s"] == "n" and rel["entity_id"] == ""


class TestLifecycle:
    """US10: entity lifecycle through property, qualifier, detach-delete and recreate."""

    def test_property_evolution_three_revisions(self):
        recs = [
            _rec(1, 1, "create_node", "node", "pump-7"),
            _rec(1, 2, "set_prop", "node", "pump-7", "status", None, "ok"),
            _rec(2, 1, "set_prop", "node", "pump-7", "status", "ok", "warn"),
            _rec(3, 1, "set_prop", "node", "pump-7", "status", "warn", "fault"),
            _rec(4, 1, "set_prop", "node", "pump-7", "status", "fault", "ok"),
        ]
        for upto, expected in ((1, "ok"), (2, "warn"), (3, "fault"), (4, "ok")):
            st = R.reconstruct([r for r in recs if r.seq <= upto], bound=None)
            assert st.nodes["pump-7"].props["status"] == expected
        assert R.compute_diff([r for r in recs if 2 <= r.seq <= 4]).to_list() == []
        assert [(r.prior, r.new) for r in recs[2:]] == [
            ("ok", "warn"),
            ("warn", "fault"),
            ("fault", "ok"),
        ]

    def test_qualifier_evolution_keeps_statement_identity(self):
        t = json.dumps(
            {"graph": None, "o": "tank-2", "p": "FEEDS", "s": "pump-7"},
            sort_keys=True,
            separators=(",", ":"),
        )
        recs = [
            _rec(2, 1, "create_rel", "rel", "42", "", None, t),
            _rec(2, 2, "set_qual", "rel", "42", "capacity", None, "100"),
            _rec(3, 1, "set_qual", "rel", "42", "capacity", "100", "120"),
            _rec(4, 1, "set_qual", "rel", "42", "verified", None, "true"),
            _rec(5, 1, "remove_qual", "rel", "42", "verified", "true", None),
        ]
        assert {r.entity_id for r in recs} == {"42"}
        for upto, expected in (
            (2, {"capacity": "100"}),
            (3, {"capacity": "120"}),
            (4, {"capacity": "120", "verified": "true"}),
            (5, {"capacity": "120"}),
        ):
            st = R.reconstruct([r for r in recs if r.seq <= upto], bound=None)
            assert st.statements["42"].quals == expected
        d = R.compute_diff([r for r in recs if r.seq >= 3]).to_list()
        assert d == [
            {
                "entity_kind": "rel",
                "entity_id": "42",
                "attr": "qual:capacity",
                "before": "100",
                "after": "120",
            }
        ]

    def test_detach_delete_cascade_records_then_node(self):
        t1 = json.dumps(
            {"graph": None, "o": "tank-2", "p": "FEEDS", "s": "pump-7"},
            sort_keys=True,
            separators=(",", ":"),
        )
        t2 = json.dumps(
            {"graph": None, "o": "pump-7", "p": "POWERS", "s": "gen-1"},
            sort_keys=True,
            separators=(",", ":"),
        )
        recs = [
            _rec(1, 1, "create_node", "node", "pump-7"),
            _rec(1, 2, "create_node", "node", "tank-2"),
            _rec(1, 3, "create_node", "node", "gen-1"),
            _rec(2, 1, "create_rel", "rel", "1", "", None, t1),
            _rec(2, 2, "create_rel", "rel", "2", "", None, t2),
            _rec(6, 1, "delete_rel", "rel", "1", "", "{}", None, None, "cascade"),
            _rec(6, 2, "delete_rel", "rel", "2", "", "{}", None, None, "cascade"),
            _rec(6, 3, "delete_node", "node", "pump-7", "", "{}", None),
        ]
        h5 = R.reconstruct([r for r in recs if r.seq < 6], bound=None)
        h6 = R.reconstruct(recs, bound=None)
        assert "pump-7" in h5.nodes and len(h5.statements) == 2
        assert "pump-7" not in h6.nodes and h6.statements == {}
        r6 = [r for r in recs if r.seq == 6]
        assert [r.op for r in r6] == ["delete_rel", "delete_rel", "delete_node"]
        assert all("cascade" in r.flags for r in r6[:2])

    def test_recreated_identifier_is_second_lifecycle(self):
        recs = [
            _rec(1, 1, "create_node", "node", "pump-7"),
            _rec(6, 1, "delete_node", "node", "pump-7", "", "{}", None),
            _rec(7, 1, "create_node", "node", "pump-7"),
        ]
        assert "pump-7" not in R.reconstruct([r for r in recs if r.seq <= 6], bound=None).nodes
        assert "pump-7" in R.reconstruct(recs, bound=None).nodes
        creates = [r for r in recs if r.op == "create_node" and r.entity_id == "pump-7"]
        assert len(creates) == 2  # two lifecycles in history
        d = R.compute_diff([r for r in recs if r.seq >= 6]).to_list()
        assert [(e["before"] is None, e["after"] is None) for e in d if e["attr"] == ""] == [
            (False, True),
            (True, False),
        ]


class TestTemporalExclusion:
    """US11: canonical reads cover SQL rows only — temporal-only edges never appear."""

    def test_read_canonical_ignores_temporal_only_edges(self):
        # ^KG("tout") rows have no SQL mirror; the fake store exposes none in rdf_edges
        conn = _FakeConn(
            {"nodes": [("a",), ("b",)], "rdf_labels": [], "rdf_props": [], "rdf_edges": []}
        )
        st = R.read_canonical(conn, "Graph_KG", lambda t: None)
        assert set(st.nodes) == {"a", "b"} and st.statements == {}

    def test_mirrored_temporal_row_is_a_structural_statement(self):
        conn = _FakeConn(
            {
                "nodes": [("a",), ("b",)],
                "rdf_labels": [],
                "rdf_props": [],
                "rdf_edges": [("a", "T", "b", "g", None)],
            }
        )
        st = R.read_canonical(conn, "Graph_KG", lambda t: None)
        assert len(st.statements) == 1 and next(iter(st.statements.values())).graph == "g"
