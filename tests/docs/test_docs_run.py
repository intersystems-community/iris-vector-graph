"""R042: the reader docs' code runs, and the answers they state match (doc-test layer 1).

One case per document in tests/docs/docs.toml. Each document starts in a freshly emptied scratch namespace
(IVG460DOC: every non-system global killed, the post-install baseline reloaded, classes a document compiled
dropped; see harness.reset_namespace). A case fails on an unmarked block that fails or cannot be classified, on an
answer that differs from the one the doc states, or on a malformed marker. Skips are reported by reason.

Needs the test container (IVG_TEST_CONTAINER, default ivg-iris-enterprise) running with IVG460DOC created:
    python scripts/dev/make_scratch_namespace.py IVG460DOC

IVG_DOCTEST_JSON=<file.json> also writes each document's summary there (the baseline in
specs/release-4.6.0/doctest-baseline.md was made that way; `python -m tests.docs.harness render <file.json>`).
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import asdict
from pathlib import Path

import pytest

from tests.conftest import requires_running_container
from tests.docs.harness import REPO, load_config, parse_document, reset_namespace, run_document, summarize

CONFIG = load_config()
WORK = Path(os.environ.get("IVG_DOCTEST_WORKDIR", Path.home() / ".cache" / "ivg-bench" / "doctest"))
RUN_ID = time.strftime("%Y%m%d-%H%M%S")

pytestmark = [pytest.mark.requires_iris, requires_running_container(CONFIG.container)]


def _record(path: str, entry: dict) -> None:
    target = os.environ.get("IVG_DOCTEST_JSON")
    if not target:
        return
    p = Path(target)
    report = json.loads(p.read_text()) if p.exists() else {}
    report[path] = entry
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, indent=2, default=str))


@pytest.mark.parametrize("entry", CONFIG.docs, ids=[d.path for d in CONFIG.docs])
def test_doc_runs(entry):
    path = REPO / entry.path
    assert path.exists(), f"{entry.path} is listed in tests/docs/docs.toml but does not exist"
    doc = parse_document(path.read_text())

    dropped = reset_namespace(CONFIG)
    workdir = WORK / RUN_ID / re.sub(r"[^\w.-]", "_", entry.path)
    notes: list = []
    results = run_document(doc, workdir=workdir, config=CONFIG, label=entry.path,
                           block_timeout=entry.block_timeout, notes=notes)
    summary = summarize(results)
    _record(entry.path, {
        "summary": asdict(summary),
        "outputs": sum(1 for r in results if r.status == "output"),
        "parse_errors": [[e.line, e.message] for e in doc.errors],
        "results": [asdict(r) for r in results],
        "notes": notes,
        "dropped_before": dropped,
    })

    print(f"\n{entry.path}: {summary.found} blocks, {summary.ok} ran ok, {summary.checked} bash checked, "
          f"{summary.failed} failed, "
          f"{summary.unclassifiable} unclassifiable, {summary.skipped} skipped, {summary.data} data")
    for reason, n in sorted(summary.skips.items()):
        print(f"  skipped x{n}: {reason}")
    for note in sorted(set(notes)):
        print(f"  note: {note}")

    problems = [f"line {e.line}: {e.message}" for e in doc.errors]
    problems += [f"line {r.line} ({r.kind}): {r.message}" for r in results
                 if r.status in ("failed", "unclassifiable")]
    assert not problems, f"{entry.path}: {len(problems)} problem(s)\n" + "\n".join(problems)
