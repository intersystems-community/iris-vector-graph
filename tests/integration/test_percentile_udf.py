"""IVG.Percentile, the UDFs behind percentileDisc and percentileCont, called directly."""

from __future__ import annotations

import pytest

from iris_vector_graph.engine import IRISGraphEngine


@pytest.fixture
def pct(iris_connection):
    obj = IRISGraphEngine(iris_connection, embedding_dimension=768)._store._iris_obj()
    return lambda method, *args: obj.classMethodValue("IVG.Percentile", method, *args)


@pytest.mark.parametrize(
    "vals,p,expected",
    [("[3,1,2]", 0.0, 1), ("[3,1,2]", 0.5, 2), ("[3,1,2]", 1.0, 3), ("[5,1,4,2,3]", 0.25, 2), ("[7]", 0.9, 7)],
)
def test_discrete_percentile_picks_a_member(pct, vals, p, expected):
    assert float(pct("PDISC", vals, p)) == expected


@pytest.mark.parametrize(
    "vals,p,expected",
    [("[1,2,3,4]", 0.5, 2.5), ("[1,2,3,4]", 0.0, 1), ("[1,2,3,4]", 1.0, 4), ("[10,20]", 0.25, 12.5), ("[9]", 0.3, 9)],
)
def test_continuous_percentile_interpolates(pct, vals, p, expected):
    assert float(pct("PCONT", vals, p)) == pytest.approx(expected)


def test_empty_input_gives_the_neutral_answer(pct):
    assert pct("PDISC", "[]", 0.5) in ("", None)
    assert float(pct("PCONT", "[]", 0.5)) == 0


@pytest.mark.parametrize("method", ["PDISC", "PCONT"])
@pytest.mark.parametrize("p", [-0.1, 1.1])
def test_out_of_range_percentile_is_an_argument_error(pct, method, p):
    with pytest.raises(RuntimeError) as caught:
        pct(method, "[1,2,3]", p)
    assert "percentile must be between 0.0 and 1.0" in str(caught.value) or "NumberOutOfRange" in str(caught.value)


def test_sort_array_orders_numbers_ascending_not_as_text(pct):
    assert float(pct("PDISC", "[10,9,100,1]", 1.0)) == 100
