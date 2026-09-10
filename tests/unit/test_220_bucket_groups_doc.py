"""Unit tests for spec 220 — get_bucket_groups docstring documents no-target limitation."""


class TestBucketGroupsDocumentation:

    def test_get_bucket_groups_docstring_documents_no_target(self):
        """T001: get_bucket_groups docstring mentions target limitation."""
        from iris_vector_graph._engine.temporal import TemporalMixin
        doc = TemporalMixin.get_bucket_groups.__doc__ or ""
        assert "target" in doc.lower(), (
            "get_bucket_groups docstring must document that target is not included"
        )
        assert "get_bucket_group_targets" in doc, (
            "get_bucket_groups docstring must reference get_bucket_group_targets workaround"
        )

    def test_get_bucket_group_targets_docstring_present(self):
        """T003: get_bucket_group_targets docstring is meaningful."""
        from iris_vector_graph._engine.temporal import TemporalMixin
        doc = TemporalMixin.get_bucket_group_targets.__doc__ or ""
        assert len(doc.strip()) > 20, "get_bucket_group_targets should have a docstring"
