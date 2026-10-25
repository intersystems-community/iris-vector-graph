"""Ablations of a generated walk: each removes one kind of work so the paired time difference is that work's cost.
Variants that change the answer are noted (their rows differ by design); the ones that must not are checked.

    python -m scripts.bench.alt.prof259.abl BI18 [rounds]
"""
import re
import sys

from scripts.bench.alt.prof259.var import *


def indent(ln):
    return re.match(r"^(\t*)", ln).group(1)


def emission_bounds(lines):
    s = next(i for i, ln in enumerate(lines) if 'Set g = $Get(pQ("cgc"' in ln)
    e = next(i for i in range(s, len(lines)) if re.match(r"^\t*Kill pB\(\d\)$", lines[i]))
    return s, e


def group_end(lines, s):
    a = next(i for i in range(s, len(lines)) if lines[i].strip() == 'If g = "" {')
    ind = indent(lines[a])
    z = next(i for i in range(a + 1, len(lines)) if lines[i] == ind + "}")
    return a, z


def v_noemit(lines):
    s, e = emission_bounds(lines)
    return lines[:s] + lines[e:]


def v_grouponly(lines):
    s, e = emission_bounds(lines)
    a, z = group_end(lines, s)
    return lines[: z + 1] + lines[e:]


def v_localcount(lines):
    """The count in a local (pQ("cnt", g)) instead of ^||IVG.CypherRun("g", g, j)."""
    return [re.sub(r'Do \$Increment\(\^\|\|IVG\.CypherRun\("g", g, 1\)(, 1)?\)', 'Set pQ("cnt", g) = $Get(pQ("cnt", g)) + 1', ln) for ln in lines]


def v_nodistinct(lines):
    """The count(DISTINCT) block replaced by a plain count (answer changes)."""
    s, e = emission_bounds(lines)
    a, z = group_end(lines, s)
    ind = indent(lines[z])
    return lines[: z + 1] + [ind + 'Do $Increment(^||IVG.CypherRun("g", g, 1))'] + lines[e:]


def v_localgroups(lines):
    """Group ids and key map in locals (pQ("lg")), not ^||IVG.CypherRun: the answer's groups are not written (answer differs)."""
    out = []
    for ln in lines:
        ln = ln.replace(
            'Set g = $Get(^||IVG.CypherRun("grpv", kv))',
            'Set g = $Get(pQ("lgv", kv))',
        )
        ln = ln.replace(
            'If g = "" { Set g = $Increment(^||IVG.CypherRun("g")), ^||IVG.CypherRun("g", g) = $Char(1)_kv, ^||IVG.CypherRun("grpv", kv) = g }',
            'If g = "" { Set g = $Increment(pQ("lgn")), pQ("lg", g) = $Char(1)_kv, pQ("lgv", kv) = g }',
        )
        ln = re.sub(r'Do \$Increment\(\^\|\|IVG\.CypherRun\("g", g, 1\)(, 1)?\)', 'Set pQ("cnt", g) = $Get(pQ("cnt", g)) + 1', ln)
        out.append(ln)
    return out


def v_noidloop(lines):
    """The relationship-identity loop run once (For e = $Char(1)): same answer where no relationship is parallel."""
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(r"^(\t*)For \{$", ln)
        if m and i + 1 < len(lines) and re.match(r"^\t*If z(\d) \{$", lines[i + 1]):
            k = re.match(r"^\t*If z(\d) \{$", lines[i + 1]).group(1)
            ind = m.group(1)
            out.append(f"{ind}For e{k} = $Char(1) {{")
            # skip the If z/Else block: ends at the line `}` closing the Else at indent+1
            j = i + 2
            while not (lines[j] == ind + "\t}" and lines[j - 1].startswith(ind + "\t\t")):
                j += 1
            i = j + 1
            continue
        out.append(ln)
        i += 1
    return out


def v_nopb(lines):
    """pB(k) kept only where the interpreter reads it (the emission and cross checks) - here: all Set/Kill pB removed and s<k> taken from m<k-1> (answer same when the emission reads pB: it does, so only valid with emission removed)."""
    out = []
    for ln in lines:
        m = re.match(r"^(\t*)Set s(\d) = pB\(([1-9])\)$", ln)
        if m:
            out.append(f"{m.group(1)}Set s{m.group(2)} = m{m.group(3)}")
            continue
        if re.match(r"^\t*(Kill pB\(\d\)|Set pB\(\d\) = m\d)$", ln):
            continue
        out.append(ln)
    return out


def v_gl(lines):
    """Group lookup/creation only, the map and ids local."""
    return v_localgroups(v_grouponly(lines))


def v_gprop(lines):
    """Group creation reads the property and builds kv, takes g = 1 (no map, no ids)."""
    out = []
    for ln in v_grouponly(lines):
        if 'Set g = $Get(^||IVG.CypherRun("grpv", kv))' in ln:
            ln = ln.replace('Set g = $Get(^||IVG.CypherRun("grpv", kv))', "Set g = 1")
        elif 'If g = "" { Set g = $Increment' in ln:
            continue
        out.append(ln)
    return out


def v_gnoprop(lines):
    """As gl, but the key is the node id: no property read."""
    out = []
    var = re.search(r'Set g = \$Get\(pQ\("cgc", gk, pB\((\d)\)\)\)', "\n".join(lines)).group(1)
    for ln in v_gl(lines):
        if re.match(r"^\t*Set r = \$Order\(", ln) and "gk, pB(" in ln:
            out.append(indent(ln) + 'Set r = ""')
            continue
        if "Set kv = kv_$Select(r =" in ln:
            out.append(indent(ln) + "Set kv = kv_$ListBuild(pB(" + var + "))")
            continue
        out.append(ln)
    return out


def v_noguard(lines):
    """The $Data guard before each neighbour loop dropped ($Order of an absent subtree is "" anyway): same answer."""
    return [ln for ln in lines if not re.match(r"^\t*Continue:'\$Data\(\^KG\(d\d,pKey,s\d,p\d\)\)$", ln)]


def v_noident(lines):
    """No relationship identity at all (a/b/e/z, the e loop, the compares): answer the same where no relationship is
    parallel or reusable (BI14, BI18 on SNB), the multiplicity check kept ($Data >= 10 not made: lone assumed)."""
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        if re.match(r"^\t*Set a\d = \$Select", ln):
            m = re.match(r"^(\t*)Set a(\d) =", ln)
            out.append(f"{m.group(1)}For e{m.group(2)} = $Char(1) {{")
            # skip to the end of the For { If z.. } block's header
            j = i + 1
            ind = m.group(1)
            assert lines[j] == ind + "For {"
            j += 1
            while not (lines[j] == ind + "\t}" and lines[j - 1].startswith(ind + "\t\t")):
                j += 1
            i = j + 1
            continue
        if re.match(r"^\t*Continue:\(a\d=a\d\)&&", ln):
            i += 1
            continue
        out.append(ln)
        i += 1
    return out


VARIANTS = {
    "noguard": v_noguard,
    "noident": v_noident,
    "gl": v_gl,
    "gprop": v_gprop,
    "gnoprop": v_gnoprop,
    "full": lambda L: L,
    "noemit": v_noemit,
    "grouponly": v_grouponly,
    "localcount": v_localcount,
    "nodistinct": v_nodistinct,
    "localgroups": v_localgroups,
    "noidloop": v_noidloop,
    "noemit+noidloop": lambda L: v_noidloop(v_noemit(L)),
    "noemit+noidloop+nopb": lambda L: v_nopb(v_noidloop(v_noemit(L))),
}
MUST_EQUAL = {"full", "localcount", "noidloop"}

if __name__ == "__main__":
    short = sys.argv[1]
    rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    picks = [int(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3 else [0]
    conn, n = connect()
    n.classMethodValue("Graph.KG.Cypher.Gen", "Mode", "sync")
    eng = engine(conn, parallel_min=-1)
    S = sets()
    q = QUERIES[NAME[short]]
    lines = source(n, HASH[short]).rstrip("\n").split("\n")
    names = []
    only = sys.argv[4].split(",") if len(sys.argv) > 4 else list(VARIANTS)
    for vn, fn in VARIANTS.items():
        if vn not in only:
            continue
        cls = "A" + short + re.sub(r"\W", "", vn)
        deploy(n, conn, cls, "\n".join(fn(lines)))
        names.append((vn, f"ZIVGP.{cls}"))
    for i in picks:
        params = {k: S[i][k] for k in q["params"] if k in S[i]}
        plan, p = capture_plan(eng, NAME[short], params)
        classes = [None] + [c for _, c in names]
        res, heads = paired(n, plan, p, classes, rounds=rounds)
        base = min(res[None])
        print(f"{short} {params}  generated: {spread(res[None])}")
        for vn, c in names:
            ok = "rows=" + ("same" if heads[c] == heads[None] else "DIFF") + ("" if vn in MUST_EQUAL or heads[c] != heads[None] else " (expected)")
            print(f"   {vn:24s} min {min(res[c]):7.2f} ms  ({min(res[c]) / base * 100:5.1f}% of generated min)  med {statistics.median(res[c]):7.2f}  {ok}")
