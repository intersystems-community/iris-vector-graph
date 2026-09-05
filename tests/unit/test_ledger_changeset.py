"""Spec 213 — unit tests for iris_vector_graph.ledger.changeset (no IRIS required).

T009: builder, wire format, canonical JSON stability, fingerprint scope (Q2), reserved names.
T046 (TestFingerprintScope) extends this module in US4.
"""

import hashlib
import json
import os

import pytest

from iris_vector_graph.ledger import changeset as C
from iris_vector_graph.ledger.errors import ChangesetOperationError, EmptyChangesetError

SKIP_IRIS_TESTS = os.environ.get("SKIP_IRIS_TESTS", "false").lower() == "true"

SCHEMA_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "specs",
    "213-graph-revision-ledger",
    "contracts",
    "changeset.schema.json",
)


def _full_changeset(**meta) -> C.Changeset:
    cs = C.Changeset(actor="ingest:etl-42", actor_type="ingest", **meta)
    cs.create_node("pump-7", labels=["Equipment"], properties={"status": "ok"})
    cs.upsert_node("tank-2", labels=["Vessel"])
    cs.add_label("pump-7", "Critical")
    cs.remove_label("pump-7", "Critical")
    cs.set_property("pump-7", "status", "warn")
    cs.remove_property("pump-7", "status")
    r = cs.create_relationship("pump-7", "FEEDS", "tank-2", qualifiers={"weight": "1.0"})
    cs.upsert_relationship("pump-7", "FEEDS", "tank-2", qualifiers={"capacity": "100"})
    cs.set_qualifier(r, "capacity", "120")
    cs.set_qualifier(("pump-7", "FEEDS", "tank-2", None), "verified", "true")
    cs.replace_qualifiers(stmt_id="1042", qualifiers={"capacity": "120"})
    cs.remove_qualifier(stmt_id="1042", key="verified")
    cs.delete_relationship(stmt_id="1042")
    cs.delete_node("pump-7", mode="detach")
    return cs


class TestBuilderWire:
    def test_all_op_kinds_serialize(self):
        wire = _full_changeset().to_wire()
        kinds = [op["op"] for op in wire["ops"]]
        assert kinds == [
            "create_node",
            "upsert_node",
            "add_label",
            "remove_label",
            "set_prop",
            "remove_prop",
            "create_rel",
            "upsert_rel",
            "set_qual",
            "set_qual",
            "replace_quals",
            "remove_qual",
            "delete_rel",
            "delete_node",
        ]
        assert wire["actor"] == "ingest:etl-42" and wire["actor_type"] == "ingest"

    def test_wire_validates_against_schema_if_jsonschema_available(self):
        jsonschema = pytest.importorskip("jsonschema")
        with open(SCHEMA_PATH) as f:
            schema = json.load(f)
        validator = jsonschema.Draft202012Validator(
            {"$ref": "#/$defs/Changeset", "$defs": schema["$defs"]}
        )
        errors = list(validator.iter_errors(_full_changeset().to_wire()))
        assert errors == [], [e.message for e in errors]

    def test_opref_serializes_as_op_ref_index(self):
        cs = C.Changeset(actor="a", actor_type="human")
        cs.create_node("x")
        r = cs.create_relationship("x", "R", "x")
        assert isinstance(r, C.OpRef) and r.index == 1
        cs.set_qualifier(r, "k", "v")
        assert cs.to_wire()["ops"][2]["ref"] == {"op_ref": 1}

    def test_tuple_ref_serializes(self):
        cs = C.Changeset(actor="a", actor_type="human")
        cs.delete_relationship(("s", "P", "o", "g"))
        assert cs.to_wire()["ops"][0]["ref"] == {
            "tuple": {"s": "s", "p": "P", "o": "o", "graph": "g"}
        }
        cs2 = C.Changeset(actor="a", actor_type="human")
        cs2.delete_relationship(("s", "P", "o"))
        assert cs2.to_wire()["ops"][0]["ref"]["tuple"]["graph"] is None

    def test_stmt_id_ref_serializes(self):
        cs = C.Changeset(actor="a", actor_type="human")
        cs.delete_relationship(stmt_id="77")
        assert cs.to_wire()["ops"][0]["ref"] == {"stmt_id": "77"}

    def test_exactly_one_ref_form_required(self):
        cs = C.Changeset(actor="a", actor_type="human")
        with pytest.raises((ValueError, TypeError)):
            cs.delete_relationship(("s", "P", "o"), stmt_id="1")
        with pytest.raises((ValueError, TypeError)):
            cs.delete_relationship()

    def test_delete_node_default_mode_is_strict(self):
        cs = C.Changeset(actor="a", actor_type="human")
        cs.delete_node("n")
        assert cs.to_wire()["ops"][0]["mode"] == "strict"

    def test_values_are_strings(self):
        cs = C.Changeset(actor="a", actor_type="human")
        cs.set_property("n", "k", 42)
        cs.set_qualifier(stmt_id="1", key="w", value=1.5)
        ops = cs.to_wire()["ops"]
        assert ops[0]["value"] == "42" and ops[1]["value"] == "1.5"


class TestReservedAndEmpty:
    @pytest.mark.parametrize("name", ["id", "__graph"])
    def test_reserved_property_rejected_immediately(self, name):
        cs = C.Changeset(actor="a", actor_type="human")
        with pytest.raises(ChangesetOperationError) as ei:
            cs.set_property("n", name, "v")
        assert ei.value.op_index == 0
        with pytest.raises(ChangesetOperationError):
            cs.create_node("n", properties={name: "v"})
        with pytest.raises(ChangesetOperationError):
            cs.remove_property("n", name)

    def test_reserved_set_exposed(self):
        assert C.RESERVED_PROPERTIES == frozenset({"id", "__graph"})

    def test_empty_changeset_raises_on_validate(self):
        cs = C.Changeset(actor="a", actor_type="human")
        with pytest.raises(EmptyChangesetError):
            cs.validate()

    def test_op_ref_out_of_range_raises_on_validate(self):
        cs = C.Changeset(actor="a", actor_type="human")
        cs.set_qualifier(C.OpRef(5), "k", "v")
        with pytest.raises(ChangesetOperationError) as ei:
            cs.validate()
        assert ei.value.op_index == 0

    def test_op_ref_must_point_to_earlier_rel_op(self):
        cs = C.Changeset(actor="a", actor_type="human")
        cs.create_node("n")
        cs.set_qualifier(C.OpRef(0), "k", "v")  # points at a node op
        with pytest.raises(ChangesetOperationError):
            cs.validate()


class TestCanonical:
    def test_canonical_json_byte_identical_regardless_of_insertion_order(self):
        a = C.Changeset(actor="a", actor_type="human")
        a.create_node("n", labels=["L1", "L2"], properties={"x": "1", "y": "2"})
        b = C.Changeset(actor="a", actor_type="human")
        b.create_node("n", labels=["L1", "L2"], properties={"y": "2", "x": "1"})
        assert a.canonical_json() == b.canonical_json()
        assert a.canonical_json().encode("utf-8") == b.canonical_json().encode("utf-8")

    def test_canonical_json_has_sorted_keys_and_no_whitespace(self):
        cs = C.Changeset(actor="a", actor_type="human", message="m")
        cs.create_node("n")
        s = cs.canonical_json()
        assert ": " not in s and ", " not in s
        assert json.loads(s) == cs.to_wire()
        assert list(json.loads(s).keys()) == sorted(json.loads(s).keys())

    def test_fingerprint_is_sha256_of_scope_subset(self):
        cs = C.Changeset(actor="a", actor_type="human", message="m", expected_head="f" * 32)
        cs.create_node("n")
        scope = {"actor": "a", "actor_type": "human", "ops": cs.to_wire()["ops"]}
        expected = hashlib.sha256(
            json.dumps(scope, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            )
        ).hexdigest()
        assert cs.fingerprint() == expected
        assert len(cs.fingerprint()) == 64


class TestFingerprintScope:
    """Q2 / FR-019: fingerprint = ops + actor + actor_type only."""

    def _base(self, **meta):
        cs = C.Changeset(actor="a", actor_type="human", **meta)
        cs.create_node("n")
        cs.set_property("n", "k", "v")
        return cs

    @pytest.mark.parametrize(
        "meta",
        [
            {"message": "different"},
            {"source": {"system": "x"}},
            {"correlation_id": "run-1"},
            {"expected_head": "0" * 32},
            {"idempotency_key": "k1"},
        ],
    )
    def test_metadata_does_not_change_fingerprint(self, meta):
        assert self._base().fingerprint() == self._base(**meta).fingerprint()

    def test_op_order_changes_fingerprint(self):
        cs = C.Changeset(actor="a", actor_type="human")
        cs.set_property("n", "k", "v")
        cs.create_node("n")
        assert cs.fingerprint() != self._base().fingerprint()

    def test_actor_changes_fingerprint(self):
        cs = C.Changeset(actor="b", actor_type="human")
        cs.create_node("n")
        cs.set_property("n", "k", "v")
        assert cs.fingerprint() != self._base().fingerprint()

    def test_actor_type_changes_fingerprint(self):
        cs = C.Changeset(actor="a", actor_type="agent")
        cs.create_node("n")
        cs.set_property("n", "k", "v")
        assert cs.fingerprint() != self._base().fingerprint()
