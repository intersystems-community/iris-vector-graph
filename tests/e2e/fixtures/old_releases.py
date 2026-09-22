"""The artifacts older releases actually wrote, as a fixture.

An upgrade test has one hard requirement: the data it starts from must have been
produced by the release it claims to come from. Hand-authoring an old archive
encodes what I believe the old format was, and the belief is the thing under
test. So `scripts/fixtures/generate_old_snapshot.py` runs each old tag — from a
git worktree of that tag, against a scratch namespace holding that tag's compiled
ObjectScript — and freezes three files per release under
`tests/fixtures/snapshots/`:

    ivg-<tag>.zip              the archive that release's own save_snapshot wrote
    ivg-<tag>.globals.ndjson   every ^KG/^NKG node in that release's layout
    ivg-<tag>.expected.json    a manifest of what was seeded and what each holds

Two files because an upgrade has two halves, and the archive only covers one:

  * **Portability.** A consumer snapshots on the old version and restores onto a
    new install. `restore_snapshot` writes into the *current* schema, so this
    half never exercises a migration — it exercises whether old *content*
    survives a shape change.
  * **In place.** A consumer upgrades the package against their existing
    database. Nothing is re-shaped for them; the globals sit at their old
    coordinates until a migration moves them. That needs the old layout
    verbatim, which is what the NDJSON carries.

The manifest is the assertion source. A test that re-derives the seed from the
archive is a test that agrees with whatever the archive says.

This module is the interface to all of that: `old_releases()` and the accessors
on `OldRelease`. Nothing outside it should know the file names or the NDJSON
shape.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SNAPSHOT_DIR = Path(__file__).resolve().parents[3] / "tests/fixtures/snapshots"


def _canonical(sub: Any) -> Any:
    """A subscript as IRIS would hold it: canonical numbers numeric, rest text."""
    text = str(sub)
    if text.lstrip("-").isdigit() and str(int(text)) == text:
        return int(text)
    return text


@dataclass(frozen=True)
class OldRelease:
    """One frozen release: its archive, its globals, and what was seeded."""

    tag: str
    zip_path: Path
    globals_path: Path
    manifest: dict
    ddl_path: Path | None = None

    @property
    def seeded(self) -> dict:
        return self.manifest["seeded"]

    @property
    def named_graph(self) -> str:
        return self.manifest["named_graph"]

    @property
    def embedding_dimension(self) -> int:
        return self.manifest["embedding_dimension"]

    def ddl_statements(self) -> list[str]:
        """The `CREATE TABLE`/`ALTER TABLE` statements this release's own
        `initialize_schema` issued, in the order it issued them.

        The content fixtures above answer "what did the old release store"; this one
        answers "what shape did it store it in", which is what a migration test needs
        to start from. Rendering the shape from the current catalog instead would make
        the test agree with whatever shape happens to be installed.

        Returns `[]` for a release frozen without `--ddl`, which is a real answer about
        what was captured rather than an error.

        Replaying them is not idempotent and is not meant to be: the list is what the
        release issues against a *live* namespace, so it includes the ALTERs it uses to
        bring an older install forward, and those fail against tables its own CREATEs
        just made correctly. A caller replays the list and tolerates per-statement
        failure, exactly as `initialize_schema` does.
        """
        if self.ddl_path is None or not self.ddl_path.exists():
            return []
        return list(json.loads(self.ddl_path.read_text())["statements"])

    def archive_metadata(self) -> dict:
        """The `metadata.json` that release wrote inside its own archive."""
        with zipfile.ZipFile(self.zip_path) as zf:
            return json.loads(zf.read("metadata.json"))

    def archive_rows(self, table: str) -> list[dict]:
        """Rows the archive carries for a table, e.g. ``Graph_KG.nodes``.

        Returns `[]` for a table the archive does not name — which is a real
        answer about an old release, not an error.
        """
        member = f"sql/{table.replace('.', '_')}.ndjson"
        with zipfile.ZipFile(self.zip_path) as zf:
            if member not in zf.namelist():
                return []
            return [
                json.loads(line)
                for line in zf.read(member).decode("utf-8").splitlines()
                if line.strip()
            ]

    def archive_global_nodes(self, global_name: str) -> list[dict]:
        """Global nodes the archive carries, as ``{"k": [...], "v": str}``."""
        member = f"globals/{global_name.lstrip('^')}.ndjson"
        with zipfile.ZipFile(self.zip_path) as zf:
            if member not in zf.namelist():
                return []
            return [
                json.loads(line)
                for line in zf.read(member).decode("utf-8").splitlines()
                if line.strip()
            ]

    def globals(self) -> list[dict]:
        """Every captured global node, as ``{"global": str, "k": [...], "v": str}``."""
        return [
            json.loads(line)
            for line in self.globals_path.read_text().splitlines()
            if line.strip()
        ]

    def write_globals(self, iris_obj) -> int:
        """Put the captured globals back at the coordinates that release used.

        This is the in-place upgrade's starting condition: a database whose globals
        were written by the old code and have not been touched since. Nothing is
        re-shaped on the way in — a loader that "fixed" a subscript would be the
        migration under test.

        A subscript that is a canonical number goes back in as a number. IRIS
        collates numeric subscripts before strings, and the whole hazard being
        tested lives in that ordering: integer `0` keys the default graph and
        collates before every timestamp, while the string `"0"` sorts elsewhere
        entirely. Restoring `"1700000000"` as text would put the temporal tree in a
        place no `$Order` walk over numbers reaches.
        """
        written = 0
        for entry in self.globals():
            subs = [_canonical(s) for s in entry["k"]]
            iris_obj.set(entry["v"], entry["global"], *subs)
            written += 1
        return written

    def globals_under(self, global_name: str, *subs: Any) -> list[dict]:
        """The captured nodes below ``^global(*subs)``, subs compared as strings.

        Subscripts come back from the Native API as strings, so `0` and `"0"` are
        the same coordinate here. Comparing them as written would make a graph key
        of `0` look absent.
        """
        want = [str(s) for s in subs]
        return [
            entry
            for entry in self.globals()
            if entry["global"] == global_name and entry["k"][: len(want)] == want
        ]


def old_releases() -> list[OldRelease]:
    """Every frozen release, oldest tag first."""
    out = []
    for manifest_path in sorted(SNAPSHOT_DIR.glob("ivg-*.expected.json")):
        stem = manifest_path.name[: -len(".expected.json")]
        manifest = json.loads(manifest_path.read_text())
        out.append(
            OldRelease(
                tag=manifest["tag"],
                zip_path=SNAPSHOT_DIR / f"{stem}.zip",
                globals_path=SNAPSHOT_DIR / f"{stem}.globals.ndjson",
                manifest=manifest,
                ddl_path=SNAPSHOT_DIR / f"{stem}.ddl.json",
            )
        )
    return out


def release(tag: str) -> OldRelease:
    """One frozen release by tag, e.g. ``v3.2.0``. Raises when it is not frozen."""
    for candidate in old_releases():
        if candidate.tag == tag:
            return candidate
    raise LookupError(
        f"no frozen snapshot for {tag}; have {[c.tag for c in old_releases()]}. "
        f"Generate one with scripts/fixtures/generate_old_snapshot.py."
    )


OLD_RELEASES = old_releases()
RELEASE_IDS = [r.tag for r in OLD_RELEASES]

#: Releases frozen before spec 223 gave the temporal globals a graph subscript.
#: Their `save_snapshot` exports `^KG("out")` and `^KG("in")` and stops, and their
#: captured globals hold `^KG("tout", ts, s, p, o)` with no graph key.
#:
#: Declared here because three test files partition the fixtures this way, and a
#: hand-kept copy per file is how `test_upgrade_migrate_globals_e2e.py` came to
#: assert a flat starting layout against the graph-scoped v3.2.0 fixture. A tag
#: list rather than a probe of each archive: asking the artifact what it holds and
#: asserting that answer back tests nothing — `tests/unit/test_old_release_fixtures.py`
#: is where each release is checked against the side it is listed under.
PRE_223_TAGS = frozenset({"v2.16.0", "v2.20.0"})

PRE_223_RELEASES = [r for r in OLD_RELEASES if r.tag in PRE_223_TAGS]
PRE_223_IDS = [r.tag for r in PRE_223_RELEASES]

GRAPH_SCOPED_RELEASES = [r for r in OLD_RELEASES if r.tag not in PRE_223_TAGS]
GRAPH_SCOPED_IDS = [r.tag for r in GRAPH_SCOPED_RELEASES]
