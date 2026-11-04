"""Generate docs/inventory.json from ADMIN_GUIDE's inventory tables (spec 262 T062).

    python scripts/dev/gen_inventory_json.py           write docs/inventory.json
    python scripts/dev/gen_inventory_json.py --check   exit 1 when docs/inventory.json is out of date

ADMIN_GUIDE is the source: an operator reads the tables, and tests/e2e/test_262_inventory.py compares the JSON with a
freshly initialized namespace. Two kinds of table are read, each directly after its marker comment:

    <!-- inventory -->
    | Kind | Names | Present | Purpose | Optional, and how it is activated | Back up |

    <!-- inventory: skipped -->
    | Class | Skipped when the server has no | Reason setup gives |

Names are the backticked words of the Names cell (`nodes`, `^KG`); Present is "after setup", "on first use" or
"when activated". A row the generator cannot read is an error, not a skipped row.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GUIDE = REPO / "docs" / "ADMIN_GUIDE.md"
OUT = REPO / "docs" / "inventory.json"

KINDS = {"package", "table", "view", "function", "procedure", "global", "storage"}
PRESENT = {"after setup": "setup", "on first use": "use", "when activated": "activation"}
_TICKED = re.compile(r"`([^`]+)`")
_MARKER = re.compile(r"^<!--\s*inventory(?::\s*(?P<which>[\w-]+))?\s*-->$")


def _cells(line: str) -> list:
    body = line.strip()
    if not (body.startswith("|") and body.endswith("|")):
        raise ValueError(f"not a table row: {line!r}")
    return [c.strip() for c in body[1:-1].split("|")]


def _tables(text: str):
    """Yield (which, header, rows) for each table that directly follows an inventory marker."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = _MARKER.match(lines[i].strip())
        if not m:
            i += 1
            continue
        which = m.group("which") or "objects"
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
        if i >= len(lines) or not lines[i].lstrip().startswith("|"):
            raise ValueError(f"ADMIN_GUIDE line {i}: an inventory marker must be followed by a table")
        header = _cells(lines[i])
        i += 2  # the header and its --- row
        rows = []
        while i < len(lines) and lines[i].lstrip().startswith("|"):
            rows.append((i + 1, _cells(lines[i])))
            i += 1
        yield which, header, rows


def _entry(lineno: int, cells: list) -> dict:
    if len(cells) != 6:
        raise ValueError(f"line {lineno}: an inventory row has 6 cells, this one {len(cells)}: {cells}")
    kind, names, present, purpose, optional, backup = cells
    if kind not in KINDS:
        raise ValueError(f"line {lineno}: kind {kind!r} is not one of {sorted(KINDS)}")
    if present not in PRESENT:
        raise ValueError(f"line {lineno}: present {present!r} is not one of {sorted(PRESENT)}")
    listed = _TICKED.findall(names)
    if not listed:
        raise ValueError(f"line {lineno}: no backticked name in {names!r}")
    return {"kind": kind, "names": listed, "present": PRESENT[present], "purpose": purpose,
            "optional": optional, "backup": backup}


def _skipped(lineno: int, cells: list) -> dict:
    if len(cells) != 3:
        raise ValueError(f"line {lineno}: a skipped-class row has 3 cells, this one {len(cells)}: {cells}")
    cls, missing, reasons = cells
    names = _TICKED.findall(cls)
    if len(names) != 1:
        raise ValueError(f"line {lineno}: one backticked class name expected in {cls!r}")
    keys = _TICKED.findall(reasons)
    if not keys:
        raise ValueError(f"line {lineno}: no backticked reason in {reasons!r}")
    return {"class": names[0], "missing": missing, "reasons": keys}


def build(text: str) -> dict:
    entries, skipped, schema = [], [], None
    m = re.search(r"<!--\s*inventory-schema:\s*(\w+)\s*-->", text)
    if m:
        schema = m.group(1)
    for which, _header, rows in _tables(text):
        if which == "objects":
            entries += [_entry(n, c) for n, c in rows]
        elif which == "skipped":
            skipped += [_skipped(n, c) for n, c in rows]
        else:
            raise ValueError(f"unknown inventory table {which!r}")
    return {
        "source": "docs/ADMIN_GUIDE.md",
        "generator": "scripts/dev/gen_inventory_json.py",
        "schema": schema,
        "entries": entries,
        "skipped_classes": skipped,
    }


def render(inventory: dict) -> str:
    return json.dumps(inventory, indent=2, ensure_ascii=False) + "\n"


def main(argv: list) -> int:
    want = render(build(GUIDE.read_text()))
    if argv[:1] == ["--check"]:
        if not OUT.exists() or OUT.read_text() != want:
            print(f"{OUT.relative_to(REPO)} is out of date: run scripts/dev/gen_inventory_json.py")
            return 1
        return 0
    OUT.write_text(want)
    data = json.loads(want)
    print(f"wrote {OUT.relative_to(REPO)}: {len(data['entries'])} rows, "
          f"{sum(len(e['names']) for e in data['entries'])} names, {len(data['skipped_classes'])} skippable classes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
