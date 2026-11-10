"""A named time zone, read from the ``tzdata`` wheel first (4.7.0 row 15).

``zoneinfo.ZoneInfo(name)`` reads the host's tz database (``/usr/share/zoneinfo``, the ``TZPATH``) before the
``tzdata`` package, so one IVG answered a historical offset by host: Europe/Stockholm in 1818 is ``+00:53:28`` on macOS
and in the wheel (a mainline build, which links Stockholm to Berlin, as the openCypher TCK expects), and ``+01:12:12``
on Debian and Ubuntu, whose ``tzdata`` is built with ``backzone``. ``zone`` reads the wheel, a dependency, first: every
host then answers as the wheel's tz release does. Without the wheel, or for a zone it lacks, the host answers.
"""

from __future__ import annotations

import datetime as dt
import functools
import importlib.resources
import zoneinfo

#: the class, for ``from_file``; the host's zones are asked of ``zoneinfo.ZoneInfo`` as it stands when called
_ZONEINFO = zoneinfo.ZoneInfo


def _from_wheel(name: str) -> dt.tzinfo:
    """``name`` from the ``tzdata`` wheel's files; ``ModuleNotFoundError`` without the wheel, ``FileNotFoundError``
    (or ``IsADirectoryError``) for a zone it lacks."""
    with importlib.resources.files("tzdata").joinpath("zoneinfo", *name.split("/")).open("rb") as f:
        return _ZONEINFO.from_file(f, key=name)


@functools.lru_cache(maxsize=None)
def zone(name: str) -> dt.tzinfo:
    """The zone ``name`` (an IANA key, ``Europe/Stockholm``): the ``tzdata`` wheel's, else the host's
    (``zoneinfo.ZoneInfo``, which raises ``ZoneInfoNotFoundError`` for a zone neither has). A name that is not a key
    (empty, absolute, or with an empty, ``.`` or ``..`` part) raises ``ValueError``, as ``ZoneInfo`` does, before any
    file is opened. One object per name, as ``ZoneInfo`` keeps one."""
    parts = name.split("/") if isinstance(name, str) else [""]
    if any(p in ("", ".", "..") for p in parts) or "\\" in name:
        raise ValueError(f"not a time zone key: {name!r}")
    try:
        return _from_wheel(name)
    except (ModuleNotFoundError, FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return zoneinfo.ZoneInfo(name)
