"""4.7.2: sync() keeps the lock-less-write marker until no lock-less writer is in flight.

`Graph.KG.Adjacency.WriterLock` marks a graph in `^IVG.AdjNoLock(graph)` when its shared writer lock is refused, so the
readers store no degree counted over an entry that may not be committed. `RemoveAll` (a rebuild, sync()) and
`RemoveGraph` (an erase) used to `Kill ^IVG.AdjNoLock` outright, while the lock-less writer's transaction could still
be open: the next reader's exclusive probe on `^IVG.AdjW` succeeded, the marker was gone, and the reader stored a count
that included the uncommitted entry.

Now the lock-less writer takes a shared lock on `^IVG.AdjNoLock(graph)` and unlocks it at once (inside a transaction
IRIS defers the unlock to commit or rollback), before it increments the marker; the rebuild kills a graph's marker only
while holding that name exclusive, taken without waiting. This file reads the class source (CI has no container);
tests/e2e/test_470_silent_writers_e2e.py proves it against IRIS: marker present after a sync with a writer open,
gone after the next once the writer closed.
"""

from __future__ import annotations

import re
from pathlib import Path

CLS = Path(__file__).resolve().parents[2] / "iris_src" / "src" / "Graph" / "KG" / "Adjacency.cls"


def _code(text: str) -> str:
    return "\n".join(re.sub(r"//.*$", "", line) for line in text.splitlines() if not line.lstrip().startswith("///"))


def _method(name: str) -> str:
    text = CLS.read_text(encoding="utf-8")
    m = re.search(rf"(?m)^ClassMethod {name}\(.*?^\}}", text, re.S)
    assert m, f"no ClassMethod {name}"
    return _code(m.group(0))


def test_the_marker_is_killed_only_by_clear_no_lock_marker():
    text = CLS.read_text(encoding="utf-8")
    kills = [
        (i, line)
        for i, line in enumerate(_code(text).splitlines())
        if re.search(r"(?i)\bkill\s+\^IVG\.AdjNoLock", line)
    ]
    assert len(kills) == 1, kills
    assert re.search(r"\^IVG\.AdjNoLock\(pGraph\)", kills[0][1])
    body = _method("ClearNoLockMarker")
    assert re.search(r"(?i)\bkill\s+\^IVG\.AdjNoLock\(pGraph\)", body)


def test_clear_takes_the_name_exclusive_without_waiting_before_it_kills():
    body = _method("ClearNoLockMarker")
    lock = re.search(r'Lock\s+\+\^IVG\.AdjNoLock\(pGraph\)#"E":0', body)
    kill = re.search(r"(?i)\bkill\s+\^IVG\.AdjNoLock\(pGraph\)", body)
    assert lock and kill and lock.start() < kill.start(), body
    # a refused probe leaves the marker: the kill is reached only through the $Test of that lock
    assert "$Test" in body[lock.end() : kill.start()], body
    # released at once, whatever the caller's transaction: "I" is the immediate unlock
    assert re.search(r'Lock\s+-\^IVG\.AdjNoLock\(pGraph\)#"E?I"', body[kill.end() :]), "the probe lock is not released"


def test_rebuild_and_erase_ask_clear_instead_of_killing():
    for name in ("RemoveAll", "RemoveGraph"):
        body = _method(name)
        assert not re.search(r"(?i)\bkill\s+\^IVG\.AdjNoLock", body), name
        assert "ClearNoLockMarker(" in body, name


def test_remove_all_walks_every_marked_graph():
    body = _method("RemoveAll")
    assert re.search(r"\$Order\(\^IVG\.AdjNoLock\(", body), body


def test_a_lockless_writer_holds_the_name_shared_before_it_increments_the_marker():
    body = _method("WriterLock")
    lock = re.search(r'Lock\s+\+\^IVG\.AdjNoLock\(pGraph\)#"S"', body)
    unlock = re.search(r'Lock\s+-\^IVG\.AdjNoLock\(pGraph\)#"S"', body)
    incr = re.search(r"\$Increment\(\^IVG\.AdjNoLock\(pGraph\)\)", body)
    assert lock and unlock and incr, body
    # the order closes the race with a sync that has just found no writer: a writer that holds the name before it
    # marks is seen by the probe; one that comes after the probe marks after the kill
    assert lock.start() < unlock.start() < incr.start(), body
    # the lock is taken on the lock-less path only (the granted path has its own)
    assert body.index("Quit 1") < lock.start(), body
