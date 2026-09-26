#!/usr/bin/env python3
"""Summarise an openCypher TCK run and compare runs.

Usage:
    summarize.py RUN_DIR [OUT.tsv] [--features DIR] [--list-ignored]
    summarize.py diff A.tsv B.tsv [--features DIR]

RUN_DIR holds behave JUnit XML (``RUN_DIR/<area>/TESTS-*.xml``), as written by
``scripts/tck/run_all.sh``. Each scenario gets one verdict:

    1  passed
    0  failed, errored or untested
    S  skipped by the harness for a reason other than an upstream @ignore;
       counted as not passed and stays in the denominator
    I  tagged @ignore upstream (on the scenario, its Examples block or its
       feature); excluded from numerator and denominator, whatever it did

The summary line is ``passed / eligible (N ignored upstream; T in total)``.

The TSV has one line per scenario, sorted: ``<verdict>\\t<key>`` where key is
``<feature file stem>.<Feature name>::<scenario name>``. Older two-column files
that use only 1/0 read the same way.

``diff`` prints the scenarios that pass in A and not in B, then the reverse.
Scenarios ignored upstream are left out of both lists.

The @ignore set is read from the feature files with behave's own parser, so
Scenario Outline rows get the same names behave gives them in JUnit.
``@skipGrammarCheck`` and ``@skipStyleCheck`` are tags for the openCypher
grammar and style tooling, not for engines, and are not treated as ignore.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

PASS, FAIL, SKIPPED, IGNORED = "1", "0", "S", "I"
VERDICTS = {PASS, FAIL, SKIPPED, IGNORED}
IGNORE_TAGS = frozenset({"ignore"})

_PREFIX = re.compile(r"^features\.[^.]+\.[^.]+\.")
_EXAMPLES_INDEX = re.compile(r" -- @(\d+)\.\d+")

DEFAULT_FEATURES = Path(__file__).resolve().parents[2] / "tests" / "tck" / "features"


def normalise_key(classname: str, name: str) -> str:
    """JUnit (classname, name) -> run-independent scenario key."""
    return f"{_PREFIX.sub('', classname.strip())}::{name.strip()}"


def upstream_ignored(features_dir: Path | str) -> set[str]:
    """Keys of every scenario (or outline row) tagged @ignore in the feature files."""
    from behave.parser import parse_file

    ignored: set[str] = set()
    for path in sorted(glob.glob(os.path.join(str(features_dir), "**", "*.feature"), recursive=True)):
        feature = parse_file(path)
        if feature is None:
            continue
        stem = Path(path).stem
        classname = f"{stem}.{feature.name}"
        ftags = set(feature.tags)
        for sc in feature.scenarios:
            stags = ftags | set(sc.tags)
            generated = getattr(sc, "scenarios", None)
            if generated is None:  # plain Scenario
                if stags & IGNORE_TAGS:
                    ignored.add(normalise_key(classname, sc.name))
                continue
            examples = list(getattr(sc, "examples", []) or [])
            for g in generated:
                tags = stags | set(g.tags)
                m = _EXAMPLES_INDEX.search(g.name)
                if m and 0 < int(m.group(1)) <= len(examples):
                    tags |= set(examples[int(m.group(1)) - 1].tags)
                if tags & IGNORE_TAGS:
                    ignored.add(normalise_key(classname, g.name))
    return ignored


def _junit_status(tc: ET.Element) -> str:
    status = tc.get("status")
    if status is None:
        tags = {c.tag for c in tc}
        status = "failed" if tags & {"failure", "error"} else "skipped" if "skipped" in tags else "passed"
    if status == "passed":
        return PASS
    if status == "skipped":
        return SKIPPED
    return FAIL


def collect(run_dir: Path | str, features_dir: Path | str | None = DEFAULT_FEATURES) -> dict[str, str]:
    """Verdict per scenario key for one run directory."""
    ignored = upstream_ignored(features_dir) if features_dir else set()
    verdicts: dict[str, str] = {}
    for f in sorted(glob.glob(os.path.join(str(run_dir), "**", "*.xml"), recursive=True)):
        for tc in ET.parse(f).getroot().iter("testcase"):
            key = normalise_key(tc.get("classname", ""), tc.get("name", ""))
            verdicts[key] = IGNORED if key in ignored else _junit_status(tc)
    return verdicts


@dataclass(frozen=True)
class Counts:
    passed: int
    failed: int
    skipped: int
    ignored: int

    @property
    def eligible(self) -> int:
        return self.passed + self.failed + self.skipped

    @property
    def total(self) -> int:
        return self.eligible + self.ignored


def counts(verdicts: dict[str, str]) -> Counts:
    vals = list(verdicts.values())
    return Counts(vals.count(PASS), vals.count(FAIL), vals.count(SKIPPED), vals.count(IGNORED))


def summary_line(c: Counts) -> str:
    return f"{c.passed} / {c.eligible} eligible ({c.ignored} ignored upstream; {c.total} scenarios in total)"


def write_tsv(verdicts: dict[str, str], path: Path | str) -> None:
    with open(path, "w") as fh:
        for key, v in sorted(verdicts.items()):
            fh.write(f"{v}\t{key}\n")


def read_tsv(path: Path | str) -> dict[str, str]:
    out: dict[str, str] = {}
    with open(path) as fh:
        for line in fh:
            if not line.strip() or "\t" not in line:
                continue
            v, key = line.rstrip("\n").split("\t", 1)
            v = v.strip()
            out[key.strip()] = v if v in VERDICTS else FAIL
    return out


@dataclass(frozen=True)
class Diff:
    only_a: list[str]
    only_b: list[str]


def diff(a: dict[str, str], b: dict[str, str], ignored: set[str] | None = None) -> Diff:
    """Keys passing in one run and not the other; upstream-ignored keys left out."""
    skip = set(ignored or ()) | {k for k, v in a.items() if v == IGNORED} | {
        k for k, v in b.items() if v == IGNORED
    }
    keys = (set(a) | set(b)) - skip
    only_a = sorted(k for k in keys if a.get(k) == PASS and b.get(k) != PASS)
    only_b = sorted(k for k in keys if b.get(k) == PASS and a.get(k) != PASS)
    return Diff(only_a, only_b)


def _main_summary(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="summarize.py", description="Summarise one TCK run.")
    ap.add_argument("run_dir")
    ap.add_argument("tsv", nargs="?")
    ap.add_argument("--features", default=str(DEFAULT_FEATURES))
    ap.add_argument("--list-ignored", action="store_true", help="print the upstream-@ignore keys")
    ns = ap.parse_args(argv)
    v = collect(ns.run_dir, ns.features)
    if not v:
        print(f"no JUnit results under {ns.run_dir}", file=sys.stderr)
        return 1
    c = counts(v)
    print(summary_line(c))
    print(f"failed {c.failed}, skipped by harness {c.skipped}")
    if ns.list_ignored:
        for k in sorted(k for k, x in v.items() if x == IGNORED):
            print("  IGNORED", k)
    if ns.tsv:
        write_tsv(v, ns.tsv)
    return 0


def _main_diff(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="summarize.py diff", description="Compare two TSV result files.")
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--features", default=str(DEFAULT_FEATURES))
    ns = ap.parse_args(argv)
    a, b = read_tsv(ns.a), read_tsv(ns.b)
    ignored = upstream_ignored(ns.features) if ns.features and os.path.isdir(ns.features) else set()
    d = diff(a, b, ignored)
    for label, run in (("A", a), ("B", b)):
        c = counts({k: (IGNORED if k in ignored else x) for k, x in run.items()})
        print(f"{label}: {summary_line(c)}")
    print(f"pass in A only: {len(d.only_a)}")
    for k in d.only_a:
        print("  A", k)
    print(f"pass in B only: {len(d.only_b)}")
    for k in d.only_b:
        print("  B", k)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "diff":
        return _main_diff(argv[1:])
    return _main_summary(argv)


if __name__ == "__main__":
    sys.exit(main())
