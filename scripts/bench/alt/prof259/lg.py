"""Prototype of lever A (group state in locals, written to ^|| once): the transform of a generated walk's lines."""
import re


def indent(ln):
    return re.match(r"^(\t*)", ln).group(1)


def lg_transform(lines):
    out = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        ind = indent(ln)
        if ln.strip() == "Set:'$Data(pQ(\"cgds\")) pQ(\"cgds\") = 0":
            out.append(ln)
            out.append(ind + "Set:'$Data(pQ(\"lgs\")) pQ(\"lgs\") = 0, pQ(\"lgn\") = +$Get(^||IVG.CypherRun(\"g\"))")
            i += 1
            continue
        if ln.strip() == "If $Length(kv) < 400 {":
            a, b, c, d = lines[i + 1 : i + 5]
            assert 'Set g = $Get(^||IVG.CypherRun("grpv", kv))' in a and "Set g = $Increment(^||IVG.CypherRun(\"g\"))" in b, (a, b)
            assert c.strip() == "} Else {" and "GroupOf" in d and lines[i + 5].strip() == "}"
            out += [
                ind + "If $Length(kv) < 400 {",
                ind + "\tIf pQ(\"lgs\") {",
                a.replace(ind + "\t", ind + "\t\t", 1),
                b.replace(ind + "\t", ind + "\t\t", 1),
                ind + "\t} Else {",
                ind + "\t\tSet g = $Get(pQ(\"lgv\", kv))",
                ind + "\t\tIf g = \"\" {",
                ind + "\t\t\tSet g = $Increment(pQ(\"lgn\")), pQ(\"lg\", g) = $Char(1)_kv, pQ(\"lgv\", kv) = g",
                ind + "\t\t\tDo:g>100000 ..LGFlush(.pQ)",
                ind + "\t\t}",
                ind + "\t}",
                ind + "} Else {",
                ind + "\tDo ..LGFlush(.pQ)",
                d,
                ind + "}",
            ]
            i += 6
            continue
        m = re.search(r'Do \$Increment\(\^\|\|IVG\.CypherRun\("g", g, (\d+)\)(?:, ([^)]*))?\)', ln)
        if m:
            j, w = m.group(1), m.group(2) or "1"
            new = (
                f'If pQ("lgs") {{ Do $Increment(^||IVG.CypherRun("g", g, {j}), {w}) }} '
                f'Else {{ Set pQ("lgc", g, {j}) = $Get(pQ("lgc", g, {j})) + {w} }}'
            )
            out.append(ln.replace(m.group(0), new))
            i += 1
            continue
        out.append(ln)
        i += 1
    return out
