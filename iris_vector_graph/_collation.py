"""Where a node id falls among IRIS subscripts (IRIS standard collation), for ranking ties the way the ObjectScript
tiers do (``^||rank(-score, id)``): canonical numbers first, by value, then every other string by character code.

A canonical number is what ObjectScript writes for a number: no leading zero before the point (``.5``), no trailing
zero after it, no ``+``, no ``-0``. So ``-1 < 0 < .5 < 1.5 < 9 < 10 < 07 < B < P1 < a``. Before 4.6.0 R306 only
integers were read as numbers here, so ``1.5`` sorted among the text where IRIS puts it among the numbers.
"""

from __future__ import annotations

import re
from decimal import Decimal

_CANONICAL = re.compile(r"0|-?(?:[1-9][0-9]*(?:\.[0-9]*[1-9])?|\.[0-9]*[1-9])")


def collation_key(node_id) -> tuple:
    s = str(node_id)
    if _CANONICAL.fullmatch(s):
        return (0, Decimal(s), "")
    return (1, Decimal(0), s)


#: Scores equal to this many decimal places are a tie, broken by id: two nodes in the same position get scores that
#: differ in the last bits of a float (or of an ObjectScript decimal), which ranked them by that noise rather than
#: by id (4.6.0 R306). The ObjectScript tiers rank by ``$Normalize(score, 12)`` the same way.
TIE_PLACES = 12


def rank_key(node_id, score) -> tuple:
    """Highest score first, ties (equal to `TIE_PLACES` decimals) by id in IRIS collation."""
    return (-round(float(score), TIE_PLACES), collation_key(node_id))
