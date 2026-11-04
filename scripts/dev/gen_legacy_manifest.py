#!/usr/bin/env python3
"""Generate the frozen 4.5.x legacy manifest: every object 3.2.0 to 4.5.1 created in `SQLUser`, the `User` package or
no package, plus the classes 4.6.0 or an earlier release stopped shipping (spec 262, research R6, FR-012; 4.6.0 R119).

    python scripts/dev/gen_legacy_manifest.py              # print the literal for iris_vector_graph/legacy_manifest.py
    python scripts/dev/gen_legacy_manifest.py --write      # write that file
    python scripts/dev/gen_legacy_manifest.py --live       # also build a namespace at each tag and cross-check

Two sources that must agree. The text: each tag's Python (`git show <tag>:<path>`) for `CREATE FUNCTION|PROCEDURE|VIEW`
statements that land in `SQLUser` (named `SQLUser.` or unqualified), and each tag's `iris_src/src` for the classes it
ships outside a package (`User.*`, and `PageRankEmbedded`, which IRIS files under `User`) with their `[ SqlProc ]`
projections. The live namespace: `scripts/fixtures/build_release_namespace.py <tag>` and its `inventory()`. `--live`
fails on any difference, except the functions the Arno bridge creates the first time an algorithm runs through Arno
(`lazy`), which setup alone never creates.

The manifest is names, never patterns: an application's own `SQLUser.CY_MINE` is not on it, so nothing removes it.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
#: Every release a namespace can upgrade from. A class a release stopped shipping stays in the namespace through every
#: later upgrade (an upgrade compiles classes and deletes none), so the scan reaches back to 3.2.0: from 4.4.0 alone it
#: missed 3.2.0-4.2.3's Graph.KG.Loader (4.6.0 R119).
TAGS = (
    "v3.2.0", "v4.0.0", "v4.1.0", "v4.1.1", "v4.2.0", "v4.2.1", "v4.2.2", "v4.2.3", "v4.3.0",
    "v4.4.0", "v4.4.1", "v4.4.2", "v4.5.0", "v4.5.1",
)
OUT = REPO / "iris_vector_graph" / "legacy_manifest.py"
#: Code classes a release stopped shipping (FR-015; R080: GraphOperators, whose SQL procedures nothing calls; R119:
#: Graph.KG.Loader, a file loader 4.3.0 dropped). Named, never found by difference: 3.2.0 also shipped table classes
#: (Graph.KG.Edge, Graph.KG.kgNodeEmbeddings) that are the projections of live tables, and deleting one drops its table.
RETIRED_CLASSES = ("Graph.KG.BenchFormat", "Graph.KG.Benchmark", "Graph.KG.Loader", "iris.vector.graph.GraphOperators")
#: A retired class whose file a release carried without its setup deploying it: a namespace built by v3.2.0 to v4.3.0
#: holds no iris.vector.graph.GraphOperators (checked with --live), so those releases are not where it came from.
DEPLOYED_FROM = {"iris.vector.graph.GraphOperators": "v4.4.0"}
#: SQLUser functions 4.6.0 creates nowhere, so they have no replacement in the IVG Schema ("" in the manifest): the
#: translator answers head() and last() inline and never called them, and their bodies failed when called.
RETIRED_FUNCTIONS = ("LIST_HEAD", "LIST_LAST")
#: Modules whose functions are created on first use, not by initialize_schema().
LAZY_MODULES = ("iris_vector_graph/stores/arno_bridge.py",)

_DDL = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(FUNCTION|PROCEDURE|VIEW)\s+((?:[\w{}%]+\.)?)(\w+)", re.IGNORECASE
)
_CLASS = re.compile(r"^Class\s+([\w.%]+)", re.M)
_SQLPROC = re.compile(r"^ClassMethod\s+(\w+)\s*\([^\n]*\[([^\]\n]*\bSqlProc\b[^\]\n]*)\]", re.M)
_SQLNAME = re.compile(r"\bSqlName\s*=\s*(\w+)")


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout


def tags_available() -> bool:
    return all(
        subprocess.run(["git", "-C", str(REPO), "rev-parse", "-q", "--verify", f"refs/tags/{t}"],
                       capture_output=True).returncode == 0
        for t in TAGS
    )


def _class_of(name: str) -> str:
    """The class IRIS compiles a `SQLUser` function or view into (research R2: `X_Y` -> `User.funcXY`)."""
    return name.replace("_", "")


def text_entries(tag: str) -> dict:
    """`{sql_name or class_name: entry}` for one tag, from its source text."""
    entries: dict = {}
    for path in _git("ls-tree", "-r", "--name-only", tag, "iris_vector_graph").split():
        if not path.endswith(".py"):
            continue
        text = _git("show", f"{tag}:{path}")
        for kind, qual, name in _DDL.findall(text):
            if qual and qual.rstrip(".").upper() != "SQLUSER":
                continue
            kind = kind.lower()
            cls = f"User.{_class_of(name)}" if kind == "view" else f"User.func{_class_of(name)}"
            entries[f"SQLUser.{name}"] = {
                "kind": kind, "sql_name": f"SQLUser.{name}", "class_name": cls,
                "replacement": "" if kind == "view" or name in RETIRED_FUNCTIONS else name,
                "lazy": path in LAZY_MODULES,
            }
    for path in _git("ls-tree", "--name-only", tag, "iris_src/src/").split():
        if not path.endswith(".cls"):
            continue
        text = _git("show", f"{tag}:{path}")
        declared = _CLASS.search(text).group(1)
        cls = declared if "." in declared else f"User.{declared}"
        short = cls.rsplit(".", 1)[1]
        procs = _SQLPROC.findall(text)
        if not procs:
            entries[cls] = {"kind": "class", "sql_name": "", "class_name": cls, "replacement": "", "lazy": False}
        for method, quals in procs:
            m = _SQLNAME.search(quals)
            sql = m.group(1) if m else f"{short}_{method}"
            entries[f"SQLUser.{sql}"] = {
                "kind": "function", "sql_name": f"SQLUser.{sql}", "class_name": cls,
                "replacement": sql, "lazy": False,
            }
    for cls in RETIRED_CLASSES:
        since = DEPLOYED_FROM.get(cls)
        if since and _version(tag) < _version(since):
            continue
        if _git("ls-tree", "--name-only", tag, f"iris_src/src/{cls.replace('.', '/')}.cls").strip():
            entries[cls] = {"kind": "class", "sql_name": "", "class_name": cls, "replacement": "", "lazy": False}
    return entries


def manifest() -> list:
    """Every entry across TAGS, with the first and last release that created it."""
    merged: dict = {}
    for tag in TAGS:
        for key, entry in text_entries(tag).items():
            version = tag.lstrip("v")
            if key not in merged:
                merged[key] = dict(entry, first_release=version, last_release=version)
            else:
                merged[key]["last_release"] = version
    return [merged[k] for k in sorted(merged)]


def live_differences(entries: list, tag: str, ns: str = "IVG262UPG") -> list:
    """What a namespace built at `tag` holds that the text does not name, and the reverse (lazy entries excepted)."""
    sys.path.insert(0, str(REPO / "scripts" / "fixtures"))
    from build_release_namespace import build

    version = tag.lstrip("v")
    inv = build(tag, ns, seed=False)

    def in_tag(e: dict) -> bool:
        return _between(e, version)

    expected_sql = {e["sql_name"].split(".", 1)[1].upper() for e in entries if e["sql_name"] and e["kind"] != "view" and not e["lazy"] and in_tag(e)}
    expected_views = {e["sql_name"].split(".", 1)[1] for e in entries if e["kind"] == "view" and in_tag(e)}
    expected_classes = {e["class_name"] for e in entries if not e["lazy"] and in_tag(e) and not e["class_name"].startswith("Graph.KG.")}
    live_sql = {name.upper() for _, name in inv["routines"]}
    live_views = set(inv["views"])
    live_classes = set(inv["classes"])
    diffs = []
    for label, want, got in (("routine", expected_sql, live_sql), ("view", expected_views, live_views),
                             ("class", expected_classes, live_classes)):
        diffs += [f"{tag}: {label} {n} in the text, not in the namespace" for n in sorted(want - got)]
        diffs += [f"{tag}: {label} {n} in the namespace, not in the text" for n in sorted(got - want)]
    return diffs


def _version(tag: str) -> tuple:
    return tuple(int(x) for x in tag.lstrip("v").split("."))


def _between(entry: dict, version: str) -> bool:
    return _version(entry["first_release"]) <= _version(version) <= _version(entry["last_release"])


def render(entries: list) -> str:
    lines = [
        '"""The frozen 4.5.x legacy manifest (spec 262, FR-012): every object IVG 3.2.0 to 4.5.1 created in `SQLUser`,',
        "the `User` package or no package, plus the code classes 4.6.0 or an earlier release stopped shipping.",
        "",
        "Generated by `scripts/dev/gen_legacy_manifest.py` from the release tags and checked against a namespace built at",
        "each one; `tests/unit/test_legacy_manifest.py` regenerates it and fails on any difference. Never edit by hand, and",
        "never match by pattern: a name that is not here is not IVG's to remove.",
        "",
        "Each entry: `kind` (function, procedure, view, class), `sql_name` (`SQLUser.<name>`, empty for a class),",
        "`class_name` (the class holding it), `replacement` (the same name in the IVG Schema; empty for a view or a retired",
        "function or class, which have none), `lazy` (created on first Arno use, not by setup), `first_release` and",
        "`last_release`.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import Dict, Tuple",
        "",
        "LEGACY_MANIFEST: Tuple[Dict[str, object], ...] = (",
    ]
    for e in entries:
        lines.append("    {")
        for k in ("kind", "sql_name", "class_name", "replacement", "lazy", "first_release", "last_release"):
            lines.append(f"        {k!r}: {e[k]!r},")
        lines.append("    },")
    lines.append(")")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    entries = manifest()
    if a.live:
        diffs = [d for tag in TAGS for d in live_differences(entries, tag)]
        for d in diffs:
            print(d, file=sys.stderr)
        if diffs:
            return 1
        print(f"live cross-check: {len(TAGS)} tags agree with the text", file=sys.stderr)
    text = render(entries)
    if a.write:
        OUT.write_text(text)
        print(f"wrote {OUT}: {len(entries)} entries", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
