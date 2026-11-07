"""4.6.0: the CHANGELOG's "Changed (breaking)" list and docs/migration/v4.6.0.md's "Breaking changes" say the same thing.

Both promise "the same list": what a 4.5.x caller must change, one line each. They drifted: papercuts (72a3b8d3)
reworded nine lines in the guide only, and fixes78 put its two lines above the CHANGELOG section's introduction.
Here each list is read item by item (a `- ` line and the lines indented under it, joined), and the two must hold the
same items in the same order. A link may point elsewhere in each (the guide's `../KNOWN_ISSUES.md` is the
CHANGELOG's `docs/KNOWN_ISSUES.md`), so link targets are left out of the comparison; their text is not.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = ROOT / "CHANGELOG.md"
GUIDE = ROOT / "docs" / "migration" / "v4.6.0.md"
_TARGET = re.compile(r"\]\([^)]*\)")


def breaking_items(text: str, heading: str, next_heading: re.Pattern) -> list:
    """The bullet items under `heading`, up to the line `next_heading` matches; an item's lines joined by spaces."""
    lines = text.splitlines()
    start = lines.index(heading)
    items: list = []
    for line in lines[start + 1:]:
        if next_heading.match(line):
            break
        if line.startswith("- "):
            items.append(line[2:].strip())
        elif line.startswith("  ") and items and line.strip():
            items[-1] += " " + line.strip()
    return items


def _changelog_items() -> list:
    # the v4.6.0 section's list, which ends at its "**Changed**" heading
    return breaking_items(CHANGELOG.read_text(), "**Changed (breaking)**", re.compile(r"^\*\*Changed\*\*$"))


def _guide_items() -> list:
    return breaking_items(GUIDE.read_text(), "## Breaking changes", re.compile(r"^## "))


def _without_targets(item: str) -> str:
    return _TARGET.sub("]()", item)


def test_both_lists_are_read():
    # the parser sees the lists this is about, so an empty answer cannot pass the next test
    assert len(_changelog_items()) > 20 and len(_guide_items()) > 20


def test_the_changelog_list_is_the_guides_line_for_line():
    cl, guide = [_without_targets(i) for i in _changelog_items()], [_without_targets(i) for i in _guide_items()]
    differ = [(n, a, b) for n, (a, b) in enumerate(zip(cl, guide)) if a != b]
    assert not differ and len(cl) == len(guide), (
        f"{len(cl)} CHANGELOG items, {len(guide)} in the guide; first differences: "
        + "; ".join(f"#{n}: CHANGELOG {a[:90]!r} / guide {b[:90]!r}" for n, a, b in differ[:3]))


def test_the_changelog_list_starts_after_its_introduction():
    lines = CHANGELOG.read_text().splitlines()
    start = lines.index("**Changed (breaking)**")
    intro = next(n for n in range(start + 1, start + 10) if lines[n].strip())
    assert lines[intro].startswith("What a 4.5.x caller must change"), lines[intro]


def test_target_free_comparison_keeps_the_link_text():
    assert _without_targets("see [Known issues](docs/KNOWN_ISSUES.md#x)") == "see [Known issues]()"
    assert _without_targets("[a](x)") != _without_targets("[b](x)")
