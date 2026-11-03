"""R042: the reader docs' code runs, and the answers they state match (doc-test layer 1).

One case per document in tests/docs/docs.toml. Each document starts in a freshly emptied scratch namespace
(IVG460DOC: every non-system global killed, the post-install baseline reloaded, classes a document compiled
dropped; see harness.reset_namespace). Skips are reported by reason.

The docs failed when this test was written (4.6.0, before workstream C). Those failures are listed in
tests/docs/run_known_failures.txt, one per line as `doc | block index | first error line`, and the test is a
ratchet, as R043's is:

* a document whose failures are all listed is XFAIL, and the reason lists them;
* a failing block not on the list FAILS (an unmarked block that fails or cannot be classified, or an answer that
  differs from the one the doc states), and so does a malformed marker, which is never listed;
* a listed block that no longer fails that way FAILS until its line is deleted, so the list only shrinks.

R045 empties the list; RELEASE_GATE=1 tests/unit/test_ci_parity_gates.py requires it empty.

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
from tests.docs.harness import (
    REPO,
    RUN_KNOWN,
    failure_keys,
    load_config,
    load_known,
    parse_document,
    ratchet,
    reset_namespace,
    run_document,
    summarize,
)

CONFIG = load_config()
WORK = Path(os.environ.get("IVG_DOCTEST_WORKDIR", Path.home() / ".cache" / "ivg-bench" / "doctest"))
RUN_ID = time.strftime("%Y%m%d-%H%M%S")
KNOWN = load_known()

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
        "workdir": str(workdir),
    })

    print(f"\n{entry.path}: {summary.found} blocks, {summary.ok} ran ok, {summary.checked} bash checked, "
          f"{summary.failed} failed, "
          f"{summary.unclassifiable} unclassifiable, {summary.skipped} skipped, {summary.data} data")
    for reason, n in sorted(summary.skips.items()):
        print(f"  skipped x{n}: {reason}")
    for note in sorted(set(notes)):
        print(f"  note: {note}")

    found = failure_keys(entry.path, results, workdir, CONFIG.namespace)
    new, gone = ratchet(entry.path, found, KNOWN)
    message = [f"line {e.line}: malformed marker: {e.message}" for e in doc.errors]
    if new:
        message.append(f"{entry.path}: {len(new)} failing block(s) not in {RUN_KNOWN.name}:")
        line_of = {r.index: r.line for r in results}
        message += [f"  block {i} (line {line_of.get(i)}): {m}" for _, i, m in new]
    if gone:
        message.append(f"{entry.path}: no longer failing this way, so delete these lines from {RUN_KNOWN.name}:")
        message += [f"  {d} | {i} | {m}" for d, i, m in gone]
    assert not message, "\n".join(message)

    if found:
        listed = "; ".join(f"block {i}: {m}" for _, i, m in sorted(found))
        pytest.xfail(f"{len(found)} known failing block(s) (R045 fixes them): {listed}")
