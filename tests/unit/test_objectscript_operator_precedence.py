"""ObjectScript evaluates strictly left to right, with no precedence between operators.

`If a = "" || b = ""` is `If ((a = "") || b) = ""`: it does not test b, and for these inputs it never
matched. That mistake shipped in ten places in iris_src (JSON helper guards, a top-K cut that never cut,
a negative-index check that raised). A comparison that follows `&&` or `||` must be parenthesised.
"""

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "iris_src" / "src"
STATEMENT = re.compile(r"\s(?:Set|Return|Quit|Continue|Goto|Do|Kill|Throw|Merge|Write|Lock|Hang)\b")
COMPARISON_AFTER_LOGIC = re.compile(r"(?:&&|\|\|)[^&|]*?(?:'=|=|'<|<|'>|>|'\[|\[|\]|'\])")


def condition(line: str):
    """The condition of an If / ElseIf / While line, or None."""
    m = re.match(r"\s*(?:If|ElseIf|While)\s+(.*)$", line)
    if not m:
        return None
    text = re.sub(r'"(?:[^"]|"")*"', '""', m.group(1)).replace("^||", "^PP")
    text = text.split("{")[0]
    stmt = STATEMENT.search(text)
    if stmt:
        text = text[: stmt.start()]
    prev = None
    while prev != text:  # collapse parenthesised groups: what is left is the top level
        prev, text = text, re.sub(r"\([^()]*\)", "P", text)
    return text


def offenders(source: str):
    return [
        (n, line.strip())
        for n, line in enumerate(source.splitlines(), 1)
        if (c := condition(line)) is not None and COMPARISON_AFTER_LOGIC.search(c)
    ]


@pytest.mark.parametrize(
    "line,bad",
    [
        ('If a = "" || b = "" {', True),
        ('If json = "" || json = "null" {', True),
        ("If topK > 0 && count >= topK Goto Done", True),
        ('If (a = "") || (b = "") {', False),
        ("If a = 1 && (b = 2) {", False),
        ('If x || $Extract(r, 1, 6) = "D" {', True),
        ('If (x) || (y = "z") Return 1', False),
        ('If N = 0 || (queryText = "") Return "[]"', False),
        ("While 'tHit && tRs.%Next() {", False),
    ],
)
def test_the_detector(line, bad):
    assert bool(offenders(line)) is bad


def test_no_condition_in_iris_src_compares_after_a_logical_operator_without_parentheses():
    found = []
    for path in sorted(SRC.rglob("*.cls")):
        for n, text in offenders(path.read_text()):
            found.append(f"{path.relative_to(SRC)}:{n}: {text}")
    assert not found, "parenthesise the comparison after && / ||:\n" + "\n".join(found)
