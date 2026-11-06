"""4.6.0 R307: duration('PT36H') is PT36H; hours do not roll into days.

openCypher keeps a duration's months, days and seconds apart (CIP2015-08-06-date-time); the TCK spells a duration
between instants in hours past 24 (`'PT269112H'`, Temporal duration.between). duration('PT36H') answered 'P1DT12H'.
Seconds still roll into minutes and minutes into hours (`PT90M` is `PT1H30M`), and a fraction of a day into hours.
"""

from __future__ import annotations

import pytest

from iris_vector_graph.cypher.translator import _parse_duration_string


@pytest.mark.parametrize("text,iso", [
    ("PT36H", "PT36H"), ("P1DT36H", "P1DT36H"), ("PT24H", "PT24H"), ("PT90M", "PT1H30M"), ("PT3600S", "PT1H"),
    ("P0.5D", "PT12H"), ("P2W", "P14D"), ("P1Y2M", "P1Y2M"), ("PT-36H", "PT-36H"),
])
def test_duration_text(text, iso):
    assert _parse_duration_string(text) == iso
