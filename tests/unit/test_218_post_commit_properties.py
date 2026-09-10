"""Unit tests for spec 218 — post_commit_properties and REVISION_ID_SENTINEL."""
import pytest
from iris_vector_graph.ledger.changeset import Changeset, REVISION_ID_SENTINEL


class TestPostCommitPropertiesField:

    def test_field_exists_with_default_empty_dict(self):
        """T001: post_commit_properties defaults to {}."""
        cs = Changeset(actor="test", actor_type="test")
        assert cs.post_commit_properties == {}

    def test_field_accepts_dict_of_dicts(self):
        """T001: can set post_commit_properties at construction."""
        cs = Changeset(
            actor="test",
            actor_type="test",
            post_commit_properties={"node-1": {"key": "$REVISION_ID"}},
        )
        assert cs.post_commit_properties == {"node-1": {"key": "$REVISION_ID"}}

    def test_to_wire_includes_post_commit_properties(self):
        """T002: to_wire() serializes post_commit_properties."""
        cs = Changeset(
            actor="test",
            actor_type="test",
            post_commit_properties={"n": {"k": "v"}},
        )
        wire = cs.to_wire()
        assert "post_commit_properties" in wire
        assert wire["post_commit_properties"] == {"n": {"k": "v"}}

    def test_to_wire_empty_post_commit_excluded_or_empty(self):
        """to_wire() with empty post_commit_properties doesn't blow up."""
        cs = Changeset(actor="test", actor_type="test")
        wire = cs.to_wire()
        # Either omitted or empty dict — both acceptable
        pcp = wire.get("post_commit_properties", {})
        assert pcp == {} or pcp is None


class TestRevisionIdSentinel:

    def test_sentinel_constant_exists(self):
        """REVISION_ID_SENTINEL constant is '$REVISION_ID'."""
        assert REVISION_ID_SENTINEL == "$REVISION_ID"

    def test_sentinel_in_post_commit_properties_is_string(self):
        """Sentinel value can be stored in post_commit_properties dict."""
        cs = Changeset(
            actor="test",
            actor_type="test",
            post_commit_properties={"audit-node": {"rev": REVISION_ID_SENTINEL}},
        )
        assert cs.post_commit_properties["audit-node"]["rev"] == "$REVISION_ID"
