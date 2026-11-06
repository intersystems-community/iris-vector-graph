"""Generate tests/tck/wip.txt from a full behave run.

Usage:
    python tests/tck/wip_generate.py

Requires ivg-iris container to be running.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
TCK_DIR = Path(__file__).parent
FEATURES_DIR = TCK_DIR / "features"
WIP_FILE = TCK_DIR / "wip.txt"

# Known @wip categories from spec — used to annotate reason comments
WIP_CATEGORY_HINTS = {
    "expressions/graph": "Neo4j-specific graph object expressions (graph.names, etc.)",
    "expressions/temporal": "IRIS temporal extension model — conflicts with standard Cypher date()/duration()",
    "expressions/pattern": "Inline pattern predicates beyond EXISTS {} — partial support",
    "clauses/call": "Procedure registration (And there exists a procedure test.*) not supported",
}


def _reason_for(classname: str, message: str) -> str:
    """Return a human-readable reason for a wip entry."""
    for prefix, reason in WIP_CATEGORY_HINTS.items():
        if prefix in classname:
            return reason
    if message:
        first_line = message.strip().splitlines()[0][:120]
        return f"failure: {first_line}"
    return "IVG does not yet support this scenario"


def generate_wip_from_junit(xml_text: str, out_path: str) -> None:
    """Parse JUnit XML text and write wip.txt to out_path."""
    root = ET.fromstring(xml_text)
    entries = []

    for suite in root.iter("testsuite"):
        for tc in suite.findall("testcase"):
            name = tc.get("name", "")
            classname = tc.get("classname", "")
            status = tc.get("status", "")

            if not status:
                if tc.find("failure") is not None or tc.find("error") is not None:
                    status = "failed"
                elif tc.find("skipped") is not None:
                    status = "skipped"
                else:
                    status = "passed"

            if status in ("failed", "skipped"):
                message = ""
                for tag in ("failure", "error"):
                    child = tc.find(tag)
                    if child is not None:
                        message = child.text or child.get("message", "")
                        break
                entries.append((classname, name, message))

    with open(out_path, "w") as f:
        f.write("# TCK wip baseline — scenarios IVG cannot yet pass. DO NOT grow this list.\n")
        f.write("# Format: feature_file::Scenario: title\n")
        f.write("# reason: <why this scenario is @wip>\n\n")

        for classname, name, message in sorted(entries):
            reason = _reason_for(classname, message)
            f.write(f"# reason: {reason}\n")
            f.write(f"{classname}::Scenario: {name}\n")


def run() -> None:
    """Run behave against all features and generate wip.txt."""
    if not FEATURES_DIR.exists():
        print("ERROR: TCK features not found. Run: git submodule update --init vendor/opencypher")
        sys.exit(1)

    print("Running behave against all TCK features (this may take several minutes)...")

    with tempfile.TemporaryDirectory() as junit_dir:
        cmd = [
            sys.executable, "-m", "behave",
            "--junit",
            f"--junit-directory={junit_dir}",
            "--no-capture",
            "--format", "null",
            str(FEATURES_DIR),
        ]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO_ROOT)

        subprocess.run(cmd, cwd=str(REPO_ROOT), env=env)

        # Combine all XML files
        combined_xml = '<testsuites>\n'
        for xml_file in sorted(Path(junit_dir).glob("*.xml")):
            # a file that does not parse raises rather than leaving its scenarios out of wip.txt
            root = ET.parse(xml_file).getroot()
            for suite in root.iter("testsuite"):
                combined_xml += ET.tostring(suite, encoding="unicode") + "\n"
        combined_xml += '</testsuites>'

        generate_wip_from_junit(combined_xml, str(WIP_FILE))

    # Count results
    wip_lines = [l for l in WIP_FILE.read_text().splitlines()
                 if l.strip() and not l.strip().startswith("#")]
    print(f"\nDone. {len(wip_lines)} scenarios written to {WIP_FILE}")
    print("Review wip.txt, then commit it alongside the harness.")


if __name__ == "__main__":
    run()
