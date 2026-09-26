"""Unit tests for IRIS→TCK type normalisation."""
from decimal import Decimal
import pytest
from tests.tck.steps.comparison import normalise_iris_value


class TestNormaliseIrisValue:
    def test_none_stays_none(self):
        assert normalise_iris_value(None, None) is None

    def test_decimal_to_float(self):
        result = normalise_iris_value(Decimal("3.14"), 3.14)
        assert isinstance(result, float)
        assert result == pytest.approx(3.14)

    def test_decimal_to_int_when_expected_int(self):
        result = normalise_iris_value(Decimal("42"), 42)
        assert result == 42
        assert isinstance(result, int)

    def test_int_string_to_int(self):
        assert normalise_iris_value("42", 0) == 42

    def test_float_string_to_float(self):
        result = normalise_iris_value("3.14", 0.0)
        assert isinstance(result, float)
        assert result == pytest.approx(3.14)

    def test_bool_string_true(self):
        assert normalise_iris_value("true", True) is True

    def test_bool_string_false(self):
        assert normalise_iris_value("false", False) is False

    def test_list_tuple_to_list(self):
        result = normalise_iris_value((1, 2, 3), [1, 2, 3])
        assert result == [1, 2, 3]
        assert isinstance(result, list)

    def test_string_passthrough(self):
        assert normalise_iris_value("Alice", "Alice") == "Alice"

    def test_float_stays_float_when_expected_int(self):
        # openCypher: 3.0 is a float, not the integer 3; the TCK tells them apart.
        assert isinstance(normalise_iris_value(3.0, 3), float)
        assert normalise_iris_value(3.5, 3) == 3.5
