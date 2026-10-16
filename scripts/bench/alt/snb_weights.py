"""Interaction weights on KNOWS for the weighted-path BI queries (BI15, BI19, BI20 reduce to a weighted shortest path).

The LDBC BI weighted-path queries weigh a friendship by how much two people interact. Here the weight of a KNOWS pair is
1 / (1 + replies between them, in either direction), so a pair that talks a lot is cheap to cross and one that never did costs 1.
It is computed from the raw CSV, identically for every engine.
"""

from __future__ import annotations

try:
    from scripts.bench.alt import snb_model as model
except ImportError:
    import snb_model as model


def interaction_counts(root):
    """{frozenset({a, b}): replies between persons a and b} (a comment replying to a post or comment of the other)."""
    creator = {}
    for r in model._rows(root, "dynamic/Post"):
        creator[("P", r["id"])] = r["CreatorPersonId"]
    comments = list(model._rows(root, "dynamic/Comment"))
    for r in comments:
        creator[("C", r["id"])] = r["CreatorPersonId"]
    counts = {}
    for r in comments:
        parent = ("P", r["ParentPostId"]) if r.get("ParentPostId") else ("C", r["ParentCommentId"]) if r.get("ParentCommentId") else None
        a, b = r["CreatorPersonId"], creator.get(parent)
        if b is not None and a != b:
            key = frozenset((a, b))
            counts[key] = counts.get(key, 0) + 1
    return counts


def knows_weights(root):
    """{(person1, person2): weight} for every KNOWS pair in the files."""
    inter = interaction_counts(root)
    out = {}
    for r in model._rows(root, "dynamic/Person_knows_Person"):
        a, b = r["Person1Id"], r["Person2Id"]
        out[(a, b)] = round(1.0 / (1 + inter.get(frozenset((a, b)), 0)), 6)
    return out


def inline(text, params):
    """The text with each $name replaced by a quoted, escaped literal (the baseline reads a $parameter of a procedure CALL as text)."""
    for name in sorted(params, key=len, reverse=True):
        esc = str(params[name]).replace("\\", "\\\\").replace("'", "\\'")
        text = text.replace("$" + name, f"'{esc}'")
    return text
