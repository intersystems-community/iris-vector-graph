"""Unit tests for iris_vector_graph.exceptions (spec 212)."""

import pytest

from iris_vector_graph.exceptions import (
    NamespaceConsistencyError,
    NamespaceMismatchWarning,
)


class TestNamespaceMismatchWarning:
    def test_is_user_warning_subclass(self):
        w = NamespaceMismatchWarning("HSANALYTICS", "USER")
        assert isinstance(w, UserWarning)

    def test_message_contains_actual_ns(self):
        w = NamespaceMismatchWarning("HSANALYTICS", "USER")
        assert "HSANALYTICS" in str(w)

    def test_message_contains_expected_ns(self):
        w = NamespaceMismatchWarning("HSANALYTICS", "USER")
        assert "USER" in str(w)

    def test_message_contains_fix_hint(self):
        w = NamespaceMismatchWarning("HSANALYTICS", "USER")
        assert "map ^KG globals" in str(w)

    def test_custom_hint_used(self):
        w = NamespaceMismatchWarning("A", "B", hint="custom hint here")
        assert "custom hint here" in str(w)

    def test_attributes_stored(self):
        w = NamespaceMismatchWarning("HSANALYTICS", "USER")
        assert w.actual_ns == "HSANALYTICS"
        assert w.expected_ns == "USER"

    def test_importable_from_package(self):
        from iris_vector_graph import NamespaceMismatchWarning as W

        assert W is NamespaceMismatchWarning


class TestNamespaceConsistencyError:
    def test_is_value_error_subclass(self):
        e = NamespaceConsistencyError("HSANALYTICS", "USER")
        assert isinstance(e, ValueError)

    def test_message_contains_actual_ns(self):
        e = NamespaceConsistencyError("HSANALYTICS", "USER")
        assert "HSANALYTICS" in str(e)

    def test_message_contains_expected_ns(self):
        e = NamespaceConsistencyError("HSANALYTICS", "USER")
        assert "USER" in str(e)

    def test_message_contains_fix_hint(self):
        e = NamespaceConsistencyError("HSANALYTICS", "USER")
        assert "map ^KG globals" in str(e)

    def test_attributes_stored(self):
        e = NamespaceConsistencyError("HSANALYTICS", "USER")
        assert e.actual_ns == "HSANALYTICS"
        assert e.expected_ns == "USER"

    def test_can_be_raised(self):
        with pytest.raises(NamespaceConsistencyError):
            raise NamespaceConsistencyError("X", "Y")

    def test_importable_from_package(self):
        from iris_vector_graph import NamespaceConsistencyError as E

        assert E is NamespaceConsistencyError
