"""Spec 229 Phase 2: the SideEffects snapshot / delta / column mapping, no container.

TCK side-effect semantics (openCypher tck-api ``SideEffectOps``): the graph state is
four sets — node ids, relationship ids, distinct label names in use, and
``(entity, key, value)`` property triples — and each column counts the set
difference in one direction. ``SideEffects.from_rows`` builds that state from the
rows IVG's tables hold, so the mapping is testable without IRIS.
"""
import pytest

from tests.tck.side_effects import (
    SIDE_EFFECT_COLUMNS,
    SideEffects,
    SideEffectsAssumptionError,
    compare_side_effects,
    parse_side_effects_table,
)


def _state(nodes=(), labels=(), props=(), edges=(), reifications=0):
    """nodes: node ids; labels: (node, label); props: (node, key, val);
    edges: (edge_id, qualifiers-json)."""
    return SideEffects.from_rows(
        nodes=[("", n) for n in nodes],
        labels=[("", s, lbl) for s, lbl in labels],
        props=[("", s, k, v) for s, k, v in props],
        edges=list(edges),
        reifications=reifications,
    )


ZERO = {c: 0 for c in SIDE_EFFECT_COLUMNS}


class TestDelta:
    def test_identical_snapshots_are_all_zero(self):
        a = _state(nodes=["n1"], labels=[("n1", "A")], props=[("n1", "k", "v")])
        b = _state(nodes=["n1"], labels=[("n1", "A")], props=[("n1", "k", "v")])
        assert a.delta_to(b) == ZERO

    def test_created_node_label_and_properties(self):
        before = _state()
        after = _state(
            nodes=["n1"], labels=[("n1", "A"), ("n1", "B")], props=[("n1", "x", 1), ("n1", "y", "s")]
        )
        assert before.delta_to(after) == dict(ZERO, **{"+nodes": 1, "+labels": 2, "+properties": 2})

    def test_negative_columns(self):
        before = _state(
            nodes=["n1", "n2"],
            labels=[("n1", "A"), ("n2", "B")],
            props=[("n1", "x", 1)],
            edges=[(7, '{"w": 1}')],
        )
        after = _state(nodes=["n2"], labels=[("n2", "B")])
        assert before.delta_to(after) == dict(
            ZERO, **{"-nodes": 1, "-labels": 1, "-properties": 2, "-relationships": 1}
        )

    def test_labels_count_distinct_names_not_assignments(self):
        # CREATE (:Label), (:Label) is `+labels 1` in the TCK (Create1 [4]).
        before = _state()
        after = _state(nodes=["a", "b"], labels=[("a", "Label"), ("b", "Label")])
        assert before.delta_to(after)["+labels"] == 1

    def test_label_already_in_graph_is_not_new(self):
        before = _state(nodes=["a"], labels=[("a", "L")])
        after = _state(nodes=["a", "b"], labels=[("a", "L"), ("b", "L")])
        assert before.delta_to(after)["+labels"] == 0

    def test_isolation_labels_are_invisible(self):
        before = _state()
        after = _state(nodes=["a"], labels=[("a", "TCK_1234abcd")])
        assert before.delta_to(after) == dict(ZERO, **{"+nodes": 1})

    def test_overwritten_property_is_plus_one_minus_one(self):
        # SET n.name = 'Michael' over 'Andres' is `+properties 1, -properties 1` (Set1 [1]).
        before = _state(nodes=["a"], props=[("a", "name", "Andres")])
        after = _state(nodes=["a"], props=[("a", "name", "Michael")])
        d = before.delta_to(after)
        assert (d["+properties"], d["-properties"]) == (1, 1)

    def test_same_value_rewrite_is_no_side_effect(self):
        before = _state(nodes=["a"], props=[("a", "name", "x")])
        after = _state(nodes=["a"], props=[("a", "name", "x")])
        assert before.delta_to(after) == ZERO

    def test_relationship_properties_come_from_qualifiers(self):
        before = _state(nodes=["a", "b"])
        after = _state(nodes=["a", "b"], edges=[(1, '{"w": "2", "s": "x"}')])
        assert before.delta_to(after) == dict(ZERO, **{"+relationships": 1, "+properties": 2})

    def test_relationship_property_update_in_place(self):
        before = _state(nodes=["a", "b"], edges=[(1, '{"w": "2"}')])
        after = _state(nodes=["a", "b"], edges=[(1, '{"w": "3"}')])
        assert before.delta_to(after) == dict(ZERO, **{"+properties": 1, "-properties": 1})

    def test_node_and_edge_property_triples_do_not_collide(self):
        # node "1" and edge 1 with the same key/value are different entities
        before = _state(nodes=["1"], props=[("1", "k", "v")])
        after = _state(nodes=["1"], props=[("1", "k", "v")], edges=[(1, '{"k": "v"}')])
        assert before.delta_to(after)["+properties"] == 1

    def test_reification_rows_are_reported_as_unexpected(self):
        before = _state()
        after = _state(reifications=1)
        assert before.unexpected_to(after) == {"rdf_reifications": 1}


class TestAbsentSnapshot:
    def test_zero_delta_distinguishable_from_absent_snapshot(self):
        a = _state()
        assert compare_side_effects(a.delta_to(_state()), {}) is None
        msg = compare_side_effects(None, {})
        assert msg is not None and "snapshot" in msg

    def test_absent_snapshot_fails_even_when_nothing_expected(self):
        assert compare_side_effects(None, {"+nodes": 0}) is not None


class TestUnmapped:
    def test_unmapped_names_every_uncovered_column(self):
        assert SideEffects.unmapped(["+labels", "+bogus", "-nodes", "~weird"]) == ["+bogus", "~weird"]

    def test_all_corpus_columns_are_mapped(self):
        corpus = ["+nodes", "-nodes", "+relationships", "-relationships",
                  "+labels", "-labels", "+properties", "-properties"]
        assert SideEffects.unmapped(corpus) == []


class TestPropertyRowAssumption:
    def test_one_row_per_node_property(self):
        s = _state(nodes=["a"], props=[("a", "x", 1), ("a", "y", 2)])
        assert len(s.properties) == 2

    def test_duplicate_node_property_rows_break_the_assumption(self):
        with pytest.raises(SideEffectsAssumptionError, match="rdf_props"):
            _state(nodes=["a"], props=[("a", "x", 1), ("a", "x", 2)])

    def test_non_object_qualifiers_break_the_assumption(self):
        with pytest.raises(SideEffectsAssumptionError, match="qualifiers"):
            _state(nodes=["a"], edges=[(1, '[1, 2]')])

    def test_null_and_empty_qualifiers_are_no_properties(self):
        s = _state(nodes=["a"], edges=[(1, None), (2, ""), (3, "{}")])
        assert len(s.properties) == 0 and len(s.relationships) == 3


class TestCompare:
    def test_match(self):
        assert compare_side_effects(dict(ZERO, **{"+nodes": 1}), {"+nodes": 1}) is None

    def test_unlisted_columns_must_be_zero(self):
        msg = compare_side_effects(dict(ZERO, **{"+nodes": 1, "+labels": 1}), {"+nodes": 1})
        assert "+labels" in msg and "expected 0" in msg and "observed 1" in msg

    def test_mismatch_names_column_expected_and_observed(self):
        msg = compare_side_effects(dict(ZERO, **{"+nodes": 2}), {"+nodes": 1})
        assert "+nodes" in msg and "expected 1" in msg and "observed 2" in msg

    def test_unmapped_column_fails(self):
        msg = compare_side_effects(ZERO, {"+bogus": 1})
        assert "unmapped side-effect column" in msg and "+bogus" in msg

    def test_unexpected_writes_fail(self):
        msg = compare_side_effects(ZERO, {}, unexpected={"rdf_reifications": 1})
        assert "rdf_reifications" in msg


class TestParseTable:
    def test_heading_row_is_the_first_pair(self):
        assert parse_side_effects_table(["+nodes", "1"], [["+labels", "2"]]) == {"+nodes": 1, "+labels": 2}

    def test_non_integer_value_is_an_error(self):
        with pytest.raises(ValueError):
            parse_side_effects_table(["+nodes", "one"], [])
