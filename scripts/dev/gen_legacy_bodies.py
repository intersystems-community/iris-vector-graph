#!/usr/bin/env python3
"""Generate the fingerprints of every body IVG shipped under a legacy-manifest name (4.6.3).

    python scripts/dev/gen_legacy_bodies.py               # print the literal for iris_vector_graph/legacy_bodies.py
    python scripts/dev/gen_legacy_bodies.py --write       # write that file
    python scripts/dev/gen_legacy_bodies.py --live v4.5.1 v4.0.0 ...
                                                          # also build a namespace at each tag and check what IRIS
                                                          # holds there is in the file (IVG463L by default)

IVG 4.5.x and earlier made its SQLUser functions with ``CREATE OR REPLACE FUNCTION SQLUser.<name>``: a namespace's
own ``JSON_VALUE`` was overwritten, and an operator who installs theirs again after upgrading has a function under a
name :data:`iris_vector_graph.legacy_manifest.LEGACY_MANIFEST` lists. ``remove_legacy_objects`` removes an object only
when its text is one a release shipped, and this file says which texts those are.

The source is every release tag before 4.6.0 (:data:`TAGS`, from v1.0.0: the 1.x and 2.x releases made the same
SQLUser functions, and a namespace upgraded from one to 4.6 still holds that body). From each tag:

* each Python file under ``iris_vector_graph/``: every string constant (``ast``; an f-string's text with its
  placeholders marked) holding ``CREATE [OR REPLACE] FUNCTION|VIEW`` of a manifest name in SQLUser (named
  ``SQLUser.`` or unqualified, as gen_legacy_manifest reads them). A function's body is the code between its outer
  braces, a view's the query after ``AS``. A body with a placeholder in it cannot be fingerprinted and is reported.
* each ``.cls`` file the tag ships (``iris_src/``, ``iris/src/``): the whole source of a class the manifest names,
  and the lines of each ``SqlProc`` method a class in the User package (or none) projects under a manifest name.

The fingerprints are of the text as :mod:`iris_vector_graph.legacy_text` normalizes it, the same functions the engine
applies to what IRIS holds. ``--live`` builds each named tag with ``scripts/fixtures/build_release_namespace.py`` (no
seed) and fails on any manifest object there whose text is not in the file. ``tests/unit/test_legacy_bodies.py``
regenerates the text half and fails on any difference.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
import warnings
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from iris_vector_graph.legacy_manifest import LEGACY_MANIFEST  # noqa: E402
from iris_vector_graph.legacy_text import (  # noqa: E402
    SOURCE_CHECKED_CLASSES,
    class_fingerprint,
    routine_fingerprint,
    view_fingerprint,
    view_query,
)

#: Every release a namespace can upgrade from: each tag before v4.6.0.
TAGS = (
    "v1.0.0", "v1.1.4", "v1.1.5", "v1.1.6", "v1.1.7", "v1.39.0", "v1.40.0", "v1.41.0", "v1.42.0", "v1.43.0",
    "v1.44.0", "v1.45.0", "v1.45.1", "v1.45.2", "v1.45.3", "v1.91.0", "v1.91.1", "v1.91.2", "v1.91.3", "v1.91.4",
    "v1.92.0", "v1.92.1", "v1.92.2", "v1.93.0", "v1.94.0", "v1.96.1", "v2.0.0", "v2.1.1", "v2.2.0", "v2.3.0",
    "v2.3.1", "v2.4.0", "v2.4.1", "v2.4.2", "v2.4.3", "v2.4.5", "v2.4.6", "v2.4.8", "v2.5.0", "v2.5.1", "v2.6.0",
    "v2.7.0", "v2.7.1", "v2.8.0", "v2.9.0", "v2.11.0", "v2.12.0", "v2.14.0", "v2.15.0", "v2.16.0", "v2.17.0",
    "v2.18.0", "v2.18.1", "v2.18.2", "v2.18.3", "v2.18.4", "v2.18.5", "v2.18.6", "v2.18.7", "v2.18.8", "v2.18.9",
    "v2.19.0", "v2.19.1", "v2.20.0", "v3.0.0", "v3.0.1", "v3.1.0", "v3.2.0", "v4.0.0", "v4.1.0", "v4.1.1",
    "v4.2.0", "v4.2.1", "v4.2.2", "v4.2.3", "v4.3.0", "v4.4.0", "v4.4.1", "v4.4.2", "v4.5.0", "v4.5.1",
)
OUT = REPO / "iris_vector_graph" / "legacy_bodies.py"
PY_ROOT = "iris_vector_graph/"
CLS_ROOTS = ("iris_src/", "iris/src/")
#: The default namespace --live builds in.
LIVE_NS = "IVG463L"

#: Manifest routine and view names, upper-cased (IRIS matches SQL names without regard to case), to the manifest's
#: spelling, which keys the file.
ROUTINES = {e["sql_name"].split(".", 1)[1].upper(): e["sql_name"].split(".", 1)[1] for e in LEGACY_MANIFEST
            if e["sql_name"] and e["kind"] != "view"}
VIEWS = {e["sql_name"].split(".", 1)[1].upper(): e["sql_name"].split(".", 1)[1] for e in LEGACY_MANIFEST
         if e["kind"] == "view"}
#: The classes whose whole source is compared (the ones removal deletes as classes).
CLASSES = set(SOURCE_CHECKED_CLASSES)

_DDL = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?(FUNCTION|VIEW)\s+((?:[\w{}%]+\.)?)(\w+)", re.I)
_CLASS = re.compile(r"^Class\s+([\w.%]+)", re.M)
_SQLPROC = re.compile(r"^(?:Class)?Method\s+(\w+)\s*\([^\n]*\[([^\]\n]*\bSqlProc\b[^\]\n]*)\]\s*\n\{\n(.*?)\n\}",
                      re.M | re.S)
_SQLNAME = re.compile(r"\bSqlName\s*=\s*(\w+)")
#: Marks an f-string's placeholder in the text of a string constant.
HOLE = "\x00"


def _git(*args: str, data: str | None = None) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True,
                          input=data).stdout


def tags_available() -> bool:
    out = subprocess.run(["git", "-C", str(REPO), "tag", "-l"], capture_output=True, text=True).stdout.split()
    return set(TAGS) <= set(out)


def _blobs(tag: str) -> list:
    """``[(path, blob)]`` of the files the scan reads at `tag`."""
    out = []
    for line in _git("ls-tree", "-r", tag, "--", PY_ROOT.rstrip("/"), *(r.rstrip("/") for r in CLS_ROOTS)).splitlines():
        meta, path = line.split("\t", 1)
        blob = meta.split()[2]
        if (path.startswith(PY_ROOT) and path.endswith(".py")) or (
                path.endswith(".cls") and path.startswith(CLS_ROOTS)):
            out.append((path, blob))
    return out


def _read_blobs(shas: list) -> dict:
    """``{blob: text}``, read in one ``git cat-file --batch``."""
    proc = subprocess.run(["git", "-C", str(REPO), "cat-file", "--batch"], input=("\n".join(shas) + "\n").encode(),
                          capture_output=True, check=True)
    raw, out, i = proc.stdout, {}, 0
    for sha in shas:
        header_end = raw.index(b"\n", i)
        size = int(raw[i:header_end].split()[2])
        start = header_end + 1
        out[sha] = raw[start:start + size].decode("utf-8", errors="replace")
        i = start + size + 1
    return out


def _strings(source: str) -> list:
    """Every string constant in a Python file; an f-string as its text with each placeholder :data:`HOLE`."""
    with warnings.catch_warnings():  # an old release's invalid escapes ('\\d' in a DDL string) warn as they parse
        warnings.simplefilter("ignore", DeprecationWarning)
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(source)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            out.append("".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else HOLE
                               for v in node.values))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
    return out


def _braced(text: str, start: int):
    """The code between the brace at or after `start` and the brace that closes it (ObjectScript strings skipped),
    or None."""
    open_at = text.find("{", start)
    if open_at < 0:
        return None
    depth, quoted = 0, False
    for i in range(open_at, len(text)):
        ch = text[i]
        if ch == '"':
            quoted = not quoted
        elif quoted:
            continue
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:i]
    return None


def _ddl_bodies(text: str, found: dict, templated: list, where: str) -> None:
    for m in _DDL.finditer(text):
        kind, qual, name = m.group(1).upper(), m.group(2), m.group(3)
        if qual and qual.rstrip(".").upper() != "SQLUSER":
            continue
        key = name.upper()
        if kind == "FUNCTION" and key in ROUTINES:
            bucket, fingerprint = "routines", routine_fingerprint
            body = _braced(text, m.end())
        elif kind == "VIEW" and key in VIEWS:
            bucket, fingerprint = "views", view_fingerprint
            as_at = re.search(r"\bAS\b", text[m.end():], re.I)
            body = None if as_at is None else text[m.end() + as_at.end():].split(";")[0]
        else:
            continue
        if body is None:
            continue
        if HOLE in body:
            templated.append(f"{where}: {name}")
            continue
        spelled = (ROUTINES if bucket == "routines" else VIEWS)[key]
        found[bucket].setdefault(spelled, set()).add(fingerprint(body))


def _cls_bodies(text: str, found: dict) -> None:
    m = _CLASS.search(text)
    if m is None:
        return
    declared = m.group(1)
    cls = declared if "." in declared else f"User.{declared}"
    if cls in CLASSES:
        found["classes"].setdefault(cls, set()).add(class_fingerprint(cls, text))
    if not cls.startswith("User.") or cls.count(".") != 1:
        return  # only a class in the User package projects into SQLUser
    short = cls.rsplit(".", 1)[1]
    for method, quals, body in _SQLPROC.findall(text):
        sql = _SQLNAME.search(quals)
        name = (sql.group(1) if sql else f"{short}_{method}").upper()
        if name in ROUTINES:
            found["routines"].setdefault(ROUTINES[name], set()).add(routine_fingerprint(body))


def bodies(tags=TAGS) -> dict:
    """``{"routines", "views", "classes": {name: sorted fingerprints}, "templated": [...]}`` across `tags`.
    ``templated`` names each DDL whose body holds an f-string placeholder, and each file that does not parse: texts
    not read, which the unit test requires to be none."""
    found = {"routines": {}, "views": {}, "classes": {}}
    templated: list = []
    seen: dict = {}
    for tag in tags:
        for path, blob in _blobs(tag):
            seen.setdefault(blob, path)
    texts = _read_blobs(sorted(seen))
    for blob, path in sorted(seen.items(), key=lambda kv: kv[1]):
        text = texts[blob]
        if path.endswith(".py"):
            if "CREATE" not in text.upper():
                continue
            try:
                strings = _strings(text)
            except SyntaxError as exc:  # reported with the templated bodies: the file's DDL was not read
                templated.append(f"{path} (blob {blob[:12]}): does not parse ({exc})")
                continue
            for s in strings:
                if "CREATE" in s.upper():
                    _ddl_bodies(s, found, templated, path)
        else:
            _cls_bodies(text, found)
    result = {kind: {k: sorted(v) for k, v in sorted(names.items())} for kind, names in found.items()}
    result["templated"] = sorted(set(templated))
    return result


def render(found: dict) -> str:
    lines = [
        '"""The fingerprints of every text IVG shipped under a legacy-manifest name (4.6.3): each SQLUser function\'s',
        "body, each SQLUser view's query and each class's source, from every release before 4.6.0.",
        "",
        "Generated by `scripts/dev/gen_legacy_bodies.py` from the release tags and checked against a namespace built at",
        "a tag (`--live`); `tests/unit/test_legacy_bodies.py` regenerates it and fails on any difference. Never edit by",
        "hand. The fingerprints are of the text as `iris_vector_graph.legacy_text` normalizes it.",
        "",
        "An object whose name the manifest lists and whose text is in none of these is not IVG's: `remove_legacy_objects`",
        "lists it as such and keeps it.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Dict, Tuple",
        "",
    ]
    for title, key, doc in (
        ("ROUTINE_BODIES", "routines", "Routine name, as the manifest spells it, to the fingerprints of its bodies."),
        ("VIEW_QUERIES", "views", "View name, as the manifest spells it, to the fingerprints of its queries."),
        ("CLASS_SOURCES", "classes", "Class name to the fingerprints of its sources."),
    ):
        lines.append(f"#: {doc}")
        lines.append(f"{title}: Dict[str, Tuple[str, ...]] = {{")
        for name, prints in found[key].items():
            lines.append(f"    {name!r}: (")
            lines.extend(f"        {p!r}," for p in prints)
            lines.append("    ),")
        lines.append("}")
        lines.append("")
    return "\n".join(lines)


def live_differences(tag: str, ns: str = LIVE_NS) -> list:
    """Each manifest object a namespace built at `tag` holds whose text the file does not list."""
    sys.path.insert(0, str(REPO / "scripts" / "fixtures"))
    import iris
    from build_release_namespace import PORT, build

    from iris_vector_graph import legacy_bodies
    from iris_vector_graph._engine.legacy import class_source

    build(tag, ns, seed=False)
    conn = iris.connect("localhost", PORT, ns, "_SYSTEM", "SYS")
    try:
        cur = conn.cursor()
        diffs, checked = [], 0
        cur.execute("SELECT ROUTINE_NAME, ROUTINE_DEFINITION FROM INFORMATION_SCHEMA.ROUTINES "
                    "WHERE ROUTINE_SCHEMA = 'SQLUser'")
        for name, body in cur.fetchall():
            key = str(name).upper()
            if key in ROUTINES:
                checked += 1
                if routine_fingerprint(body) not in legacy_bodies.ROUTINE_BODIES.get(ROUTINES[key], ()):
                    diffs.append(f"{tag}: routine SQLUser.{name}'s body is not in the file")
        cur.execute("SELECT TABLE_NAME, VIEW_DEFINITION FROM INFORMATION_SCHEMA.VIEWS WHERE TABLE_SCHEMA = 'SQLUser'")
        for name, definition in cur.fetchall():
            key = str(name).upper()
            if key in VIEWS:
                checked += 1
                if view_fingerprint(view_query(definition)) not in legacy_bodies.VIEW_QUERIES.get(VIEWS[key], ()):
                    diffs.append(f"{tag}: view SQLUser.{name}'s query is not in the file")
        native = iris.createIRIS(conn)
        marks = ", ".join("?" for _ in CLASSES)
        cur.execute(f"SELECT Name FROM %Dictionary.ClassDefinition WHERE Name IN ({marks})", sorted(CLASSES))
        for (cls,) in cur.fetchall():
            cls = str(cls)
            checked += 1
            if class_fingerprint(cls, class_source(native, cls)) not in legacy_bodies.CLASS_SOURCES.get(cls, ()):
                diffs.append(f"{tag}: class {cls}'s source is not in the file")
        print(f"{tag}: {checked} manifest objects checked, {len(diffs)} unknown", file=sys.stderr)
        return diffs
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--live", nargs="*", metavar="TAG")
    ap.add_argument("--namespace", default=LIVE_NS)
    a = ap.parse_args()
    found = bodies()
    for t in found["templated"]:
        print(f"templated, not fingerprinted: {t}", file=sys.stderr)
    text = render(found)
    if a.write:
        OUT.write_text(text)
        print(f"wrote {OUT}: {len(found['routines'])} routines, {len(found['views'])} views, "
              f"{len(found['classes'])} classes", file=sys.stderr)
    elif a.live is None:
        sys.stdout.write(text)
    if a.live is not None:
        diffs = [d for tag in a.live for d in live_differences(tag, a.namespace)]
        for d in diffs:
            print(d, file=sys.stderr)
        return 1 if diffs else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
