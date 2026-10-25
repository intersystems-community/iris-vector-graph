"""Write cls/Prof.cls: ZIVGP.Prof, a subclass of Graph.KG.Cypher.Exec holding timed copies of RunA, EmitGroups, Parallel
and Part (taken from the checkout's Exec.cls), so the phases of a run can be read. Nothing here is product code.

Timers accumulate in ^||ZT(name) (process-private) in seconds; ^||ZWalk, when set, is the class whose Walk1 replaces
the generated one ("interp" for the interpreter).
"""

import re

SRC = "iris_src/src/Graph/KG/Cypher/Exec.cls"
text = open(SRC).read()


def method(name):
    m = re.search(r"^ClassMethod %s\(.*?^}\n" % name, text, re.S | re.M)
    assert m, name
    return m.group(0)


def at(s, content, before=(), after=(), replace=None, nth=None):
    """The one line of s whose stripped text is `content`: lines before/after it (same indent), or replaced."""
    lines = s.split("\n")
    idx = [i for i, ln in enumerate(lines) if ln.strip() == content]
    assert (nth is not None and idx) or len(idx) == 1, (content, len(idx))
    i = idx[nth or 0]
    ind = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
    mid = [ind + replace] if replace is not None else [lines[i]]
    new = [ind + b for b in before] + mid + [ind + a for a in after]
    return "\n".join(lines[:i] + new + lines[i + 1 :])


def rep(s, old, new, n=None):
    assert s.count(old) >= 1 and (n is None or s.count(old) == n), (old, s.count(old))
    return s.replace(old, new)


# ---- RunA
run = method("RunA")
T = 'Do $Increment(^||ZT("%s"), $ZH - %s)'
run = run.replace("{\n", "{\n\tSet tZ0 = $ZH\n", 1)
run = at(run, "// counted for the tests (spec 257): which runs walked compiled", before=['Set:$Get(^||ZWalk)\'="" tGen = $Select(^||ZWalk = "interp": "", 1: ^||ZWalk)'])
run = at(run, "Kill ^||IVG.CypherRun", before=[T % ("setup", "tZ0"), "Set tZL = $ZH"], nth=0)
run = at(run, "Kill ^||IVG.CypherRun", before=["Set tZ1 = $ZH"], after=[T % ("kill", "tZ1")], nth=-1)
run = at(run, "Do ..Anchors(tSpec, tScoped, tGraph)", before=["Set tZ1 = $ZH"], after=[T % ("anchors", "tZ1")])
walk = 'Set tZ1 = $ZH Do $ClassMethod(tGen, "Walk1", .tQ, %s, tGName, .tB) ' + T % ("walk", "tZ1")
run = rep(run, 'Do $ClassMethod(tGen, "Walk1", .tQ, tKey, tGName, .tB)', walk % "tKey", 1)
run = rep(run, 'Do $ClassMethod(tGen, "Walk1", .tQ, g, tGName, .tB)', walk % "g", 1)
iw = 'Set tZ1 = $ZH Do ..Walk(.tQ, 1, %s, tGName, .tB, ",") ' + T % ("walk", "tZ1")
run = rep(run, 'Do ..Walk(.tQ, 1, tKey, tGName, .tB, ",")', iw % "tKey", 1)
run = rep(run, 'Do ..Walk(.tQ, 1, g, tGName, .tB, ",")', iw % "g", 1)
run = rep(
    run,
    'Do:tQ("acc")&&(tQ("accw")>0) ..Group(.tQ, tGName, .tB, tQ("accw"))',
    'If tQ("acc")&&(tQ("accw")>0) { Set tZ1 = $ZH Do ..Group(.tQ, tGName, .tB, tQ("accw")) ' + T % ("accgroup", "tZ1") + " }",
    2,
)
run = at(
    run,
    "// a worker's compiled-walk count(DISTINCT) members, kept in the run's arrays, go where Export reads them; nothing",
    before=[T % ("loop", "tZL"), "Set tZ1 = $ZH", "Do ..LGFlush(.tQ)", T % ("lgflush", "tZ1")],
)
run = at(
    run,
    "Do:(pShare'=\"\")&&$Data(tQ(\"cgd\")) ..SpillDistinct(.tQ)",
    before=["Set tZ1 = $ZH"],
    replace="Do:(pShare'=\"\")&&$Data(tQ(\"cgd\"))&&'$Get(^||ZHash) ..SpillDistinct(.tQ)",
    after=[T % ("spill", "tZ1")],
)
# prototype of lever D: with ^||ZHash = k the workers split on a hash of hop k's node, so that variable's members are
# disjoint across workers (its count(DISTINCT) adds up, no member set is exchanged)
run = at(
    run,
    'Set:$Order(^||IVG.CypherRun("cand", ""), -1)>1 tQ("splitvar") = ""',
    after=['Set:$Get(^||ZHash)&&(tQ("split")="h1") tQ("splitvar") = $List(tQ("h", ^||ZHash), 2)'],
)
run = at(run, "Do ..Export(.tQ, pShare)", before=["Set tZ1 = $ZH"], after=[T % ("export", "tZ1")])
run = at(run, "Do ..EmitGroups(.tQ)", before=["Set tZ1 = $ZH"], replace="Do ..EmitGroupsT(.tQ)", after=[T % ("emitgroups", "tZ1")])
run = at(run, "Do ..EmitSorted(.tQ, $Name(^||IVG.CypherRun(\"sort\")), 1)", before=["Set tZ1 = $ZH"], after=[T % ("emitsorted", "tZ1")])
run = rep(run, "..Parallel(pPlan,", "..ParallelT(pPlan,")
# a sample of a label scan's anchors (^||ZStride: every n-th), to profile a plan too long to run whole
run = at(run, "Set tPos = tPos + 1", after=["Continue:$Get(^||ZStride)&&((tPos#^||ZStride)'=0)"], nth=-1)

# ---- EmitGroups with timers
eg = method("EmitGroups")
eg = rep(eg, "ClassMethod EmitGroups(", "ClassMethod EmitGroupsT(", 1)
eg = eg.replace("{\n", "{\n\tSet tZ0 = $ZH\n", 1)
lines = eg.split("\n")
# the second "For {" at method level (the sort filing loop) is preceded by the bound block; mark by the first line of it
j = [i for i, ln in enumerate(lines) if ln.strip() == "Set tG = $Order(^||IVG.CypherRun(\"g\", tG), 1, tKeyJSON)"]
assert len(j) == 1
k = j[0] - 1  # the "For {"
assert lines[k].strip() == "For {"
lines[k:k] = ["\t" + T % ("eg_bound", "tZ0"), "\tSet tZ0 = $ZH"]
eg = "\n".join(lines)
eg = at(eg, 'Do:tN>0 ..EmitSorted(.pQ, $Name(^||IVG.CypherRun("sort")), 1)', before=[T % ("eg_file", "tZ0"), "Set tZ0 = $ZH"], after=[T % ("eg_emit", "tZ0")])

# ---- Parallel and Part with timers
par = method("Parallel")
par = rep(par, "ClassMethod Parallel(", "ClassMethod ParallelT(", 1)
par = par.replace("{\n", "{\n\tSet tZ0 = $ZH\n", 1)
par = at(par, "Kill ^IRIS.Temp.IVG.Cypher(tShare)", after=['Set ^IRIS.Temp.IVG.Cypher(tShare, "zwalk") = $Get(^||ZWalk)']) if False else par
lines = par.split("\n")
j = [i for i, ln in enumerate(lines) if ln.strip() == "Kill ^IRIS.Temp.IVG.Cypher(tShare)"][0]
lines[j] = lines[j] + '\n\tSet ^IRIS.Temp.IVG.Cypher(tShare, "zwalk") = $Get(^||ZWalk), ^IRIS.Temp.IVG.Cypher(tShare, "zhash") = $Get(^||ZHash)'
par = "\n".join(lines)
par = at(par, "Set tJSON = pPlan.%ToJSON(), tWQ = $System.WorkMgr.%New(, tN)", before=[T % ("par_prep", "tZ0"), "Set tZ0 = $ZH"])
par = rep(
    par,
    '$$$ThrowOnError(tWQ.Queue("##class(Graph.KG.Cypher.Exec).Part", tJSON, tShare, tI, tN))',
    'If $Get(^||ZSerial) { Merge ^||ZSave = ^||ZT  Kill ^||ZT  Set tSC = ..Part(tJSON, tShare, tI, tN)  Kill ^||ZT  Merge ^||ZT = ^||ZSave  Kill ^||ZSave } Else { $$$ThrowOnError(tWQ.Queue("##class(ZIVGP.Prof).Part", tJSON, tShare, tI, tN)) }',
    1,
)
par = at(par, "$$$ThrowOnError(tWQ.WaitForComplete())", before=[T % ("par_queue", "tZ0"), "Set tZ0 = $ZH"], after=[T % ("par_wait", "tZ0"), "Set tZ0 = $ZH"])
par = at(par, 'Set tAggs = pQ("aggs"), tK = ""', before=['Merge ^||ZW = ^IRIS.Temp.IVG.Cypher(tShare, "zt")'])
# the aggregate branch's final Kill (the last one) is followed by Quit tN
idx = par.rfind("Kill ^IRIS.Temp.IVG.Cypher(tShare)")
par = par[:idx] + T % ("par_merge", "tZ0") + '\n\tIf $Get(^||ZKeep) { Kill ^||ZShare  Merge ^||ZShare = ^IRIS.Temp.IVG.Cypher(tShare) }\n\t' + par[idx:]
part = method("Part")
part = at(part, "Set ^||IVG.VLWorker = 1", after=["Set tZS = $ZH", 'Set ^||ZWalk = $Get(^IRIS.Temp.IVG.Cypher(pShare, "zwalk")), ^||ZHash = $Get(^IRIS.Temp.IVG.Cypher(pShare, "zhash"))', "Kill ^||ZT"])
part = at(part, "Kill ^||IVG.VLWorker", before=["Set tZE = $ZH - tZS", 'Set ^IRIS.Temp.IVG.Cypher(pShare, "zt", pPart) = tZE', 'Merge ^IRIS.Temp.IVG.Cypher(pShare, "zt", pPart) = ^||ZT'])

driver = r'''
/// Run pPlan as Exec.Run does (family A only), the phases timed in ^||ZT; answer Head's JSON.
ClassMethod Time(pPlan As %String, pParams As %String = "{}") As %String
{
	Set tZ0 = $ZH
	Set tPlan = ##class(%Library.DynamicObject).%FromJSON(pPlan)
	Set tTag = $Increment(^||IVG.CypherOut)
	Kill ^||IVG.CypherOut(tTag)
	Do ##class(Graph.KG.Cypher.Props).Begin()
	Do $Increment(^||ZT("begin"), $ZH - tZ0)
	Try {
		Set tCount = ..RunA(tPlan, tTag, .tWorkers, , , , .tAnchor)
	} Catch tEx {
		Do ##class(Graph.KG.Cypher.Props).End()
		Kill ^||IVG.CypherOut(tTag)
		Throw tEx
	}
	Set tZ0 = $ZH
	Do ##class(Graph.KG.Cypher.Props).End()
	Do $Increment(^||ZT("end"), $ZH - tZ0)
	Set tZ0 = $ZH
	Set tHead = ..Head(tTag, tCount, tWorkers, tAnchor)
	Do $Increment(^||ZT("head"), $ZH - tZ0)
	Quit tHead
}

/// The phase times so far as JSON (milliseconds), then cleared.
ClassMethod Phases() As %String
{
	Set tO = {}, tK = ""
	For {
		Set tK = $Order(^||ZT(tK), 1, tV)
		Quit:tK=""
		Do tO.%Set(tK, +$FNumber(tV * 1000, "", 3))
	}
	Set tW = {}, tK = ""
	For {
		Set tK = $Order(^||ZW(tK), 1, tV)
		Quit:tK=""
		Set tP = {}
		Do tP.%Set("total", +$FNumber(tV * 1000, "", 3))
		Set tN = ""
		For {
			Set tN = $Order(^||ZW(tK, tN), 1, tV2)
			Quit:tN=""
			Do tP.%Set(tN, +$FNumber(tV2 * 1000, "", 3))
		}
		Do tW.%Set(tK, tP)
	}
	Do tO.%Set("workers", tW)
	Kill ^||ZT, ^||ZW
	Quit tO.%ToJSON()
}

/// Prototype of spec 259 lever A: a compiled walk's group state kept in locals (pQ("lg"), ("lgv"), ("lgc")), written to
/// ^||IVG.CypherRun("g"/"grpv") here, once; from then on (pQ("lgs")) the walk uses ^||IVG.CypherRun as the shipped one does.
ClassMethod LGFlush(ByRef pQ)
{
	Quit:$Get(pQ("lgs"))
	Set pQ("lgs") = 1
	Set tG = ""
	For {
		Set tG = $Order(pQ("lg", tG), 1, tK)
		Quit:tG=""
		Set ^||IVG.CypherRun("g", tG) = tK, ^||IVG.CypherRun("grpv", $Extract(tK, 2, *)) = tG
	}
	Set tG = ""
	For {
		Set tG = $Order(pQ("lgc", tG))
		Quit:tG=""
		Set tJ = ""
		For {
			Set tJ = $Order(pQ("lgc", tG, tJ), 1, tC)
			Quit:tJ=""
			Do $Increment(^||IVG.CypherRun("g", tG, tJ), tC)
		}
	}
	Set:+$Get(pQ("lgn"))>+$Get(^||IVG.CypherRun("g")) ^||IVG.CypherRun("g") = pQ("lgn")
	Kill pQ("lg"), pQ("lgv"), pQ("lgc")
}

/// The compiled plan's per-node cross filters, as text.
ClassMethod Cross(pPlan As %String) As %String
{
	Set pl = ##class(%Library.DynamicObject).%FromJSON(pPlan)
	Do ..Compile(pl, .tQ)
	Do ..CrossFilters(pl, .tQ, pl.%Get("anchor"))
	Set o = "", i = ""
	For {
		Set i = $Order(tQ("n", i))
		Quit:i=""
		Set x = $Get(tQ("n", i, "x")), o = o_"node"_i_" lab="_$ListLength(tQ("n", i, "labels"))_" props="_$ListLength(tQ("n", i, "props"))_" cmp="_$ListLength(tQ("n", i, "cmp"))_" x:"
		For j = 1:1:$ListLength(x) {
			Set c = $List(x, j)
			Set o = o_" ["_$List(c, 1)_" "_$List(c, 2)_" node"_$List(c, 3)_"."_$List(c, 4)_"]"
		}
		Set o = o_$Char(10)
	}
	Quit o
}

/// Clear the timers.
ClassMethod Reset()
{
	Kill ^||ZT, ^||ZW
}
'''

out = ["Class ZIVGP.Prof Extends Graph.KG.Cypher.Exec", "{", ""]
out.append("/// Timed copies of Exec's RunA, EmitGroups, Parallel and Part (spec 259 profiling; made by mkprof.py). Not product code.")
for m in (run, eg, par, part):
    out.append(m)
    out.append("")
out.append(driver)
out.append("}")
open("scripts/bench/alt/prof259/cls/Prof.cls", "w").write("\n".join(out) + "\n")
print("written")
