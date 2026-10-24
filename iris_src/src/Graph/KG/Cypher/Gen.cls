/// Native plans compiled to routines of their own (spec 257).
///
/// Graph.KG.Cypher.Exec interprets a plan: every match passes through Walk, Neighbours, NodeOK, Emit and Group, each
/// reading the plan back out of pQ. For a plan with one hop or more (fixed or variable-length), this class writes
/// the walk as one method of a class of its own, IVGC.P<n> (extending Exec): a For loop per hop with its types and
/// direction written in, each node's id, label and property checks inline (the properties read from rdf_props'
/// storage globals by name, as Graph.KG.Cypher.Props resolved them), and the grouping (count, count(DISTINCT))
/// inline. Cross filters, projections, ORDER BY and DISTINCT call Exec's own methods. RunA asks For once per run and
/// calls the class's Walk1 in place of Walk(.., 1, ..); everything before and after the walk is the interpreter's.
///
/// A shape is the plan without its parameter values. ^IVG.CypherCompiled(hash) = $ListBuild(class or "", shape,
/// stamp, status, built at); ("src", i) the lines of the class's Walk1 (Source reads them). The stamp is rdf_props' compile time and
/// the executor's and this class's build times: an entry whose stamp is not the running one is built again. Status
/// "pending", "ok", or the build's error, in which case the shape stays interpreted (and the error was written to
/// messages.log once).
///
/// Mode, per process: "job" (the default, or ^IVG.CypherGen("mode")) runs a built shape compiled and builds a new one
/// in a background job, answering interpreted meanwhile; "sync" builds a new shape before it runs; "off" never runs
/// compiled (the shadow reference).
Class Graph.KG.Cypher.Gen Extends %RegisteredObject
{

/// The package generated classes live in; DropAll deletes it.
Parameter PACKAGE = "IVGC";

/// Set this process's mode ("job", "sync" or "off"; "" goes back to the namespace default) and answer the mode now
/// in effect.
ClassMethod Mode(pMode As %String = "") As %String
{
	If pMode = "" {
		Kill ^||IVG.CypherGenMode
	} ElseIf $ListFind($ListBuild("job", "sync", "off"), pMode) {
		Set ^||IVG.CypherGenMode = pMode
	} Else {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "Graph.KG.Cypher.Gen: unknown mode "_pMode))
	}
	Quit ..Effective()
}

/// The mode in effect in this process.
ClassMethod Effective() As %String
{
	Quit $Get(^||IVG.CypherGenMode, $Get(^IVG.CypherGen("mode"), "job"))
}

/// The mode a run walks in: its plan's (pQ("genmode"), a worker's coordinator's), else this process's.
ClassMethod ModeOf(ByRef pQ) As %String
{
	Quit $Select($Get(pQ("genmode")) '= "": pQ("genmode"), 1: ..Effective())
}

/// This class's build time, part of every entry's stamp: a generator changed by a deploy writes other code.
ClassMethod BuiltAt() As %String [ CodeMode = objectgenerator ]
{
	Do %code.WriteLine(" Quit """_$ZDateTime($ZTimestamp, 3, 1, 6)_"""")
	Quit $$$OK
}

/// The compiled class to walk the plan in pQ with (Exec.Compile's arrays, after CrossFilters), or "" to interpret it.
/// pQ("genmode"), when set, is the mode (a worker runs its coordinator's).
ClassMethod For(ByRef pQ, pAnchor As %Integer) As %String
{
	Set tMode = ..ModeOf(.pQ)
	Quit:tMode="off" ""
	// the generated code reads properties from rdf_props' storage globals; a layout Props cannot read directly is
	// read by the interpreter, through SQL
	Quit:'pQ("pd") ""
	Quit:'..Covered(.pQ) ""
	Set tShape = ..Shape(.pQ, pAnchor), tStamp = ..Stamp(), tH = $ZCRC(tShape, 7)
	Set tE = $Get(^IVG.CypherCompiled(tH))
	If (tE '= "") && ($List(tE, 2) = tShape) && ($List(tE, 3) = tStamp) {
		Quit:$List(tE,4)="ok" $List(tE, 1)
		// a failed build is not tried again; a pending one is left to its job unless that job died
		Quit:$List(tE,4)'="pending" ""
		Quit:(..Now()-$List(tE,5))<120 ""
	}
	// test hook (test_257_compiled_plans_e2e): a process that may not write ^IVG.CypherCompiled
	Throw:$Get(^IVG.CypherGen("breakfor")) ##class(%Exception.General).%New("<PROTECT>", 0, , "breakfor test hook")
	Set ^IVG.CypherCompiled(tH) = $ListBuild("", tShape, tStamp, "pending", ..Now())
	Kill ^IVG.CypherCompiled(tH, "src")
	Try {
		Do ..Lines(.pQ, pAnchor, .tL)
	} Catch tEx {
		// a shape the generator cannot write stays interpreted, as a failed build does
		Set $List(^IVG.CypherCompiled(tH), 4) = "not written: "_$Extract(tEx.DisplayString(), 1, 400)
		Return ""
	}
	Merge ^IVG.CypherCompiled(tH, "src") = tL
	If tMode = "job" {
		// one attempt, no wait: a job that does not start leaves the entry pending, tried again after 120 s, and a
		// process not allowed to start one keeps the shape interpreted
		Try {
			Job ##class(Graph.KG.Cypher.Gen).Build(tH)::0
		} Catch {
		}
		Quit ""
	}
	Do ..Build(tH)
	Set tE = $Get(^IVG.CypherCompiled(tH))
	Quit:(tE'="")&&($List(tE,4)="ok") $List(tE, 1)
	Quit ""
}

/// Whether the generator writes this plan's walk: one hop or more (a plan with none is the anchor alone).
ClassMethod Covered(ByRef pQ) As %Boolean [ Private ]
{
	Quit pQ("hops") >= 1
}

/// The plan's shape: everything the generated code is written from, and none of the parameter values it reads from pQ
/// at run time (property values, comparison arguments, ids, the LIMIT).
ClassMethod Shape(ByRef pQ, pAnchor As %Integer) As %String [ Private ]
{
	Set tS = $ListBuild(##class(Graph.KG.Cypher.Exec).PlanVersion(), pAnchor, pQ("mult"), pQ("acc"), pQ("agg"), pQ("ret"), pQ("kvars"), $Get(pQ("aggs")))
	For tK = 1:1:pQ("hops") {
		// a variable-length hop's list is a fixed hop's: only its "vl" node tells them apart
		Set tS = tS_$ListBuild(pQ("h", tK), $Data(pQ("h", tK, "tally")), $Data(pQ("h", tK, "vl")))
	}
	Set tI = ""
	For {
		Set tI = $Order(pQ("n", tI))
		Quit:tI=""
		Set (tP, tC) = "", tProps = pQ("n", tI, "props"), tCmp = pQ("n", tI, "cmp")
		For tJ = 1:1:$ListLength(tProps) { Set tP = tP_$ListBuild($List($List(tProps, tJ), 1)) }
		For tJ = 1:1:$ListLength(tCmp) { Set tC = tC_$ListBuild($List($List(tCmp, tJ), 1, 2)) }
		Set tS = tS_$ListBuild(tI, pQ("n", tI, "labels"), tP, tC, $Data(pQ("n", tI, "id")), $Get(pQ("n", tI, "x")))
	}
	Quit tS
}

/// What an entry's code was written against: rdf_props' layout (Props' stamp, read by Begin) and the builds of the
/// executor and of this generator.
ClassMethod Stamp() As %String [ Private ]
{
	Quit $Get(^||IVG.CypherProps("stamp"))_"|"_##class(Graph.KG.Cypher.Exec).BuiltAt()_"|"_..BuiltAt()
}

ClassMethod Now() As %Integer [ Private ]
{
	Quit ($Piece($Horolog, ",", 1) * 86400) + $Piece($Horolog, ",", 2)
}

/// Build entry pH's class from its lines: IVGC.P<pH>, extending the executor, with the one method Walk1. Runs in the
/// caller ("sync") or a background job; a build already running elsewhere is left to finish.
ClassMethod Build(pH As %String) As %Status
{
	Set tSC = $$$OK
	Lock +^IVG.CypherCompiled(pH):0
	Quit:'$Test $$$OK
	Try {
		Set tE = $Get(^IVG.CypherCompiled(pH))
		// no lines, no class: an empty Walk1 would answer every query with nothing
		If (tE = "") || ($List(tE, 4) '= "pending") || '$Data(^IVG.CypherCompiled(pH, "src")) {
			Lock -^IVG.CypherCompiled(pH)
			Return $$$OK
		}
		Set tCls = ..#PACKAGE_".P"_pH
		Set tStream = ##class(%Stream.GlobalCharacter).%New()
		Do tStream.WriteLine("/// Written by Graph.KG.Cypher.Gen (spec 257) for one native plan shape; a deploy drops it.")
		Do tStream.WriteLine("Class "_tCls_" Extends Graph.KG.Cypher.Exec")
		Do tStream.WriteLine("{")
		Do tStream.WriteLine("")
		Do tStream.WriteLine("ClassMethod Walk1(ByRef pQ, pKey, pGName As %String, ByRef pB)")
		Do tStream.WriteLine("{")
		Set tI = ""
		For {
			Set tI = $Order(^IVG.CypherCompiled(pH, "src", tI), 1, tLine)
			Quit:tI=""
			Do tStream.WriteLine(tLine)
		}
		// test hook (test_257_compiled_plans_e2e): a build that fails to compile
		Do:$Get(^IVG.CypherGen("breaktest")) tStream.WriteLine($Char(9)_"Set x = (")
		Do tStream.WriteLine("}")
		Do tStream.WriteLine("")
		Do tStream.WriteLine("}")
		Do tStream.Rewind()
		Set tSC = $System.OBJ.LoadStream(tStream, "-d")
		Set:$$$ISOK(tSC) tSC = $System.OBJ.Compile(tCls, "ck-d")
		If $$$ISOK(tSC) {
			Set $List(^IVG.CypherCompiled(pH), 1) = tCls, $List(^IVG.CypherCompiled(pH), 4) = "ok"
		} Else {
			Set tErr = $Extract($System.Status.GetErrorText(tSC), 1, 500)
			Set $List(^IVG.CypherCompiled(pH), 4) = tErr
			Do ##class(%SYS.System).WriteToConsoleLog("IVG native plan "_tCls_" did not compile, interpreted instead: "_tErr, 0, 1)
			Do $System.OBJ.Delete(tCls, "-d")
		}
	} Catch tEx {
		Set tSC = tEx.AsStatus()
		Set:$Data(^IVG.CypherCompiled(pH)) $List(^IVG.CypherCompiled(pH), 4) = $Extract($System.Status.GetErrorText(tSC), 1, 500)
	}
	Lock -^IVG.CypherCompiled(pH)
	Quit tSC
}

/// Delete every generated class and entry in this namespace (a deploy calls this).
ClassMethod DropAll() As %Status
{
	Set tSC = $System.OBJ.DeletePackage(..#PACKAGE, "-d")
	Kill ^IVG.CypherCompiled
	Quit tSC
}

/// Entry pH's Walk1, as written (for reading a compiled plan).
ClassMethod Source(pH As %String) As %String
{
	Set tOut = "", tI = ""
	For {
		Set tI = $Order(^IVG.CypherCompiled(pH, "src", tI), 1, tLine)
		Quit:tI=""
		Set tOut = tOut_tLine_$Char(10)
	}
	Quit tOut
}

/// The entries as JSON: [{"hash", "class", "status", "stamp"}, ...].
ClassMethod Entries() As %String
{
	Set tOut = [], tH = ""
	For {
		Set tH = $Order(^IVG.CypherCompiled(tH), 1, tE)
		Quit:tH=""
		Do tOut.%Push({"hash": (tH), "class": ($List(tE, 1)), "status": ($List(tE, 4)), "stamp": ($List(tE, 3))})
	}
	Quit tOut.%ToJSON()
}

// ------------------------------------------------------------------------------------------------ the code written

/// Walk1's lines, one per pL(i), each with its leading tabs. Its locals: s<k>, d<k>, p<k>, m<k> hop k's source,
/// direction, type and neighbour; n<k> its relationships to m<k> (parallel ones), w<k> a tallied hop's count, a<k>,
/// b<k>, e<k> the relationship a hop that must not reuse one took; k<i> node i's checks, by node; id<i>, pv<i>x<j>,
/// ca<i>x<j> node i's id, j-th property value and comparison argument.
ClassMethod Lines(ByRef pQ, pAnchor As %Integer, Output pL) [ Private ]
{
	Kill pL
	Set pL = 0
	// test hook (test_257_compiled_plans_e2e): a shape the generator fails on
	$$$ThrowOnError($Select($Get(^IVG.CypherGen("breakwrite")): $$$ERROR($$$GeneralError, "breakwrite"), 1: $$$OK))
	Do ..Add(.pL, 1, "Set tMF = pQ(""mayfill""), tLim = pQ(""limit""), tH1 = (pQ(""split"") = ""h1""), tPart = pQ(""part""), tParts = pQ(""parts"")")
	Do ..Add(.pL, 1, "Set gk = $Select(pGName = """": $Char(0), 1: pGName)")
	Do ..Add(.pL, 1, "Set:'$Data(pQ(""cgds"")) pQ(""cgds"") = 0")
	Set tI = ""
	For {
		Set tI = $Order(pQ("n", tI))
		Quit:tI=""
		Continue:tI=pAnchor
		Do:$Data(pQ("n",tI,"id")) ..Add(.pL, 1, "Set id"_tI_" = pQ(""n"", "_tI_", ""id"")")
		For tJ = 1:1:$ListLength(pQ("n", tI, "props")) {
			Do ..Add(.pL, 1, "Set pv"_tI_"x"_tJ_" = $List($List(pQ(""n"", "_tI_", ""props""), "_tJ_"), 2)")
		}
		For tJ = 1:1:$ListLength(pQ("n", tI, "cmp")) {
			Do ..Add(.pL, 1, "Set ca"_tI_"x"_tJ_" = $List($List(pQ(""n"", "_tI_", ""cmp""), "_tJ_"), 3)")
		}
	}
	Set tBound(pAnchor) = ""
	Do ..Hop(.pQ, 1, .tBound, 1, .pL)
}

/// Hop pK's loops at indent pT (or, past the last hop, the match's emission). pBound: the nodes bound before this hop.
ClassMethod Hop(ByRef pQ, pK As %Integer, ByRef pBound, pT As %Integer, ByRef pL) [ Private ]
{
	If pK > pQ("hops") {
		Do ..Emission(.pQ, "1", pT, .pL)
		Quit
	}
	Set tHop = pQ("h", pK), tFrom = $List(tHop, 1), tTo = $List(tHop, 2), tDir = $List(tHop, 3), tTypes = $List(tHop, 4)
	Set tVs = "s"_pK, tT = pT
	Do ..Add(.pL, tT, "Set "_tVs_" = pB("_tFrom_")")
	If $Data(pQ("h", pK, "vl")) {
		Set pBound(tTo) = ""
		Do ..VarHop(.pQ, pK, .pBound, tT, .pL)
		Quit
	}
	Set tUnique = $List(tHop, 5), tTally = $Data(pQ("h", pK, "tally")), tOnce = $Data(pBound(tTo))
	Set pBound(tTo) = ""
	Set tVd = "d"_pK, tVp = "p"_pK, tVm = "m"_pK
	Set tAdj = "^KG("_tVd_", pKey, "_tVs_", "_tVp_", "_tVm_")"
	If tTally {
		Do ..Add(.pL, tT, "Set w"_pK_" = 0, f"_pK_" = 0")
		// outgoing, the far node checked for nothing, each parallel relationship a match: the out-degree, from
		// Graph.KG.Adj's cache where it holds (Exec.Walk's own rule)
		If (tDir = "out") && pQ("mult") && 'tOnce && ..Unchecked(.pQ, tTo) {
			Do ..Add(.pL, tT, "If 'tMF"_$Select(pK = 1: "&&'tH1", 1: "")_" {")
			If tTypes = "" {
				Do ..Add(.pL, tT + 1, "Set tW = ##class(Graph.KG.Adj).Cached(pKey, "_tVs_")")
			} Else {
				Do ..Add(.pL, tT + 1, "Set tW = 0")
				Do ..Add(.pL, tT + 1, "For tTy = "_..Lits(tTypes)_" {")
				Do ..Add(.pL, tT + 2, "Set tD = ##class(Graph.KG.Adj).Cached(pKey, "_tVs_", tTy)")
				Do ..Add(.pL, tT + 2, "If tD = """" { Set tW = """" Quit }")
				Do ..Add(.pL, tT + 2, "Set tW = tW + tD")
				Do ..Add(.pL, tT + 1, "}")
			}
			Do ..Add(.pL, tT + 1, "If tW '= """" {")
			Do ..Add(.pL, tT + 2, "Set ^||IVG.CypherDegreeTally = $Get(^||IVG.CypherDegreeTally) + 1, f"_pK_" = 1")
			Do ..Add(.pL, tT + 2, "If tW > 0 {")
			Do ..Emission(.pQ, "tW", tT + 3, .pL)
			Do ..Add(.pL, tT + 2, "}")
			Do ..Add(.pL, tT + 1, "}")
			Do ..Add(.pL, tT, "}")
		}
		Do ..Add(.pL, tT, "If 'f"_pK_" {")
		Set tT = tT + 1
	}
	Do ..Add(.pL, tT, "For "_tVd_" = "_$Select(tDir = "both": """out"", ""in""", 1: ..Lit(tDir))_" {")
	Set tT = tT + 1
	If tTypes '= "" {
		Do ..Add(.pL, tT, "For "_tVp_" = "_..Lits(tTypes)_" {")
		Do ..Add(.pL, tT + 1, "Continue:'$Data(^KG("_tVd_",pKey,"_tVs_","_tVp_"))")
	} Else {
		Do ..Add(.pL, tT, "Set "_tVp_" = """"")
		Do ..Add(.pL, tT, "For {")
		Do ..Add(.pL, tT + 1, "Set "_tVp_" = $Order(^KG("_tVd_", pKey, "_tVs_", "_tVp_"))")
		Do ..Add(.pL, tT + 1, "Quit:"_tVp_"=""""")
	}
	Set tT = tT + 1
	If tOnce {
		// a hop closing the chain on a node it bound already (a cycle) only checks the relationship to it
		Do ..Add(.pL, tT, "Set "_tVm_" = pB("_tTo_")")
		Do ..Add(.pL, tT, "For o"_pK_" = 1 {")
		Do ..Add(.pL, tT + 1, "Quit:'$Data("_tAdj_")")
	} Else {
		Do ..Add(.pL, tT, "Set "_tVm_" = """"")
		Do ..Add(.pL, tT, "For {")
		Do ..Add(.pL, tT + 1, "Set "_tVm_" = $Order("_tAdj_")")
		Do ..Add(.pL, tT + 1, "Quit:"_tVm_"=""""")
	}
	Set tT = tT + 1
	Do ..Add(.pL, tT, "Quit:tMF&&(pQ(""count"")>=tLim)")
	// "both" reads a self-loop through its out entry only: its in entry is the same relationship
	Do:tDir="both" ..Add(.pL, tT, "Continue:("_tVd_"=""in"")&&("_tVm_"="_tVs_")")
	Do:pK=1 ..Add(.pL, tT, "If tH1 { Continue:(($Increment(pQ(""h1pos""))-1)#tParts)'=tPart }")
	If 'tOnce {
		// past the first hop a node is met once per path to it: where its checks read properties, their answer is
		// kept for the anchor's walk (k<i>), as no check but a cross filter (NodeOK, uncached) reads another node
		If (pK > 1) && ($Get(pQ("n",tTo,"x")) = "") && ((pQ("n",tTo,"props") '= "") || (pQ("n",tTo,"cmp") '= "")) {
			Set tVk = "k"_tTo
			Do ..Add(.pL, tT, "If $Data("_tVk_"("_tVm_"), ok) {")
			Do ..Add(.pL, tT + 1, "Continue:'ok")
			Do ..Add(.pL, tT, "} Else {")
			Do ..Add(.pL, tT + 1, "Set "_tVk_"("_tVm_") = 0")
			Do ..Check(.pQ, tTo, tVm, tT + 1, .pL)
			Do ..Add(.pL, tT + 1, "Set "_tVk_"("_tVm_") = 1")
			Do ..Add(.pL, tT, "}")
		} Else {
			Do ..Check(.pQ, tTo, tVm, tT, .pL)
		}
	}
	If 'tUnique {
		Set tVn = "n"_pK
		Do ..Add(.pL, tT, "Set "_tVn_" = 1")
		If pQ("mult") {
			Do ..Add(.pL, tT, "If $Data("_tAdj_") >= 10 {")
			Do ..Add(.pL, tT + 1, "Set "_tVn_" = 0, e"_pK_" = """"")
			Do ..Add(.pL, tT + 1, "For {")
			Do ..Add(.pL, tT + 2, "Set e"_pK_" = $Order(^KG("_tVd_", pKey, "_tVs_", "_tVp_", "_tVm_", e"_pK_"))")
			Do ..Add(.pL, tT + 2, "Quit:e"_pK_"=""""")
			Do ..Add(.pL, tT + 2, "Set:e"_pK_"'=""#"" "_tVn_" = "_tVn_" + 1")
			Do ..Add(.pL, tT + 1, "}")
			Do ..Add(.pL, tT, "}")
		}
		If tTally {
			Do ..Add(.pL, tT, "Set w"_pK_" = w"_pK_" + "_tVn)
		} Else {
			Do:'tOnce ..Add(.pL, tT, "Set pB("_tTo_") = "_tVm)
			If 'pQ("mult") {
				Do ..Hop(.pQ, pK + 1, .pBound, tT, .pL)
			} ElseIf (pK = pQ("hops")) && (pQ("acc") || pQ("agg")) {
				// the match counts n times: added at once
				Do ..Emission(.pQ, tVn, tT, .pL)
			} Else {
				Do ..Add(.pL, tT, "For i"_pK_" = 1:1:"_tVn_" {")
				Do ..Hop(.pQ, pK + 1, .pBound, tT + 1, .pL)
				Do ..Add(.pL, tT, "}")
			}
			Do:'tOnce ..Add(.pL, tT, "Kill pB("_tTo_")")
		}
	} Else {
		// another hop could match this relationship: each is told apart by its key (e<k>: $Char(1) for a lone
		// relationship, which has none) and used once per match, so it is compared with the relationship of every
		// earlier such hop whose types it shares (Exec.Neighbours keeps the used ones as a string of hashes)
		Set tVe = "e"_pK, tVz = "z"_pK
		Do ..Add(.pL, tT, "Set a"_pK_" = $Select("_tVd_"=""out"": "_tVs_", 1: "_tVm_"), b"_pK_" = $Select("_tVd_"=""out"": "_tVm_", 1: "_tVs_"), "_tVe_" = """", "_tVz_" = ($Data("_tAdj_") < 10)")
		Do ..Add(.pL, tT, "For {")
		Do ..Add(.pL, tT + 1, "If "_tVz_" {")
		Do ..Add(.pL, tT + 2, "Quit:"_tVe_"=$Char(1)")
		Do ..Add(.pL, tT + 2, "Set "_tVe_" = $Char(1)")
		Do ..Add(.pL, tT + 1, "} Else {")
		Do ..Add(.pL, tT + 2, "Set "_tVe_" = $Order(^KG("_tVd_", pKey, "_tVs_", "_tVp_", "_tVm_", "_tVe_"))")
		Do ..Add(.pL, tT + 2, "Quit:"_tVe_"=""""")
		Do ..Add(.pL, tT + 2, "Continue:"_tVe_"=""#""")
		Do ..Add(.pL, tT + 1, "}")
		For tJ = 1:1:(pK - 1) {
			// a variable-length hop's relationships are not kept (Exec.Walk passes the used ones through it)
			Continue:'$List(pQ("h", tJ), 5)||$Data(pQ("h",tJ,"vl"))
			Set tOther = $List(pQ("h", tJ), 4), tShare = (tTypes = "") || (tOther = "")
			For tI = 1:1:$ListLength(tTypes) { Set:$ListFind(tOther, $List(tTypes, tI)) tShare = 1 }
			Continue:'tShare
			Do ..Add(.pL, tT + 1, "Continue:(a"_pK_"=a"_tJ_")&&(b"_pK_"=b"_tJ_")&&("_tVp_"=p"_tJ_")&&("_tVe_"=e"_tJ_")")
		}
		Do:'tOnce ..Add(.pL, tT + 1, "Set pB("_tTo_") = "_tVm)
		Do ..Hop(.pQ, pK + 1, .pBound, tT + 1, .pL)
		Do:'tOnce ..Add(.pL, tT + 1, "Kill pB("_tTo_")")
		Do ..Add(.pL, tT, "}")
	}
	// close the neighbour, type and direction loops
	For tJ = 1:1:3 {
		Set tT = tT - 1
		Do ..Add(.pL, tT, "}")
	}
	If tTally {
		Do ..Add(.pL, tT, "If w"_pK_" > 0 {")
		Do ..Emission(.pQ, "w"_pK, tT + 1, .pL)
		Do ..Add(.pL, tT, "}")
		Do ..Add(.pL, tT - 1, "}")
	}
}

/// Variable-length hop pK (its source already in s<k>), as Exec.Walk walks it: its ends, distinct, from the run's
/// cache, the named ends (Exec.NamedEnds) or Graph.KG.VarLength.EndsInto, into en<k>; then either counted
/// (pQ("vlcount"), a lone hop under count(DISTINCT) of its end) or each end checked, bound and walked on from.
ClassMethod VarHop(ByRef pQ, pK As %Integer, ByRef pBound, pT As %Integer, ByRef pL) [ Private ]
{
	Set tHop = pQ("h", pK), tTo = $List(tHop, 2), tDir = ..Lit($List(tHop, 3))
	Set tVs = "s"_pK, tVm = "m"_pK, tEn = "en"_pK, tVi = "i"_pK, tVL = "pQ(""h"", "_pK_", ""vl"")", tT = pT
	// Output arguments are not killed on entry: the ends of the last source would stay behind
	Do ..Add(.pL, tT, "Kill "_tEn)
	Do ..Add(.pL, tT, "If $Data(pQ(""vlends"", $ListBuild(pGName, "_tVs_"))) {")
	Do ..Add(.pL, tT + 1, "Merge "_tEn_" = pQ(""vlends"", $ListBuild(pGName, "_tVs_"))")
	Do ..Add(.pL, tT, "} ElseIf '..NamedEnds(.pQ, "_tTo_", "_tVs_", "_tVL_", "_tDir_", pGName, .pB, ."_tEn_") {")
	Do ..Add(.pL, tT + 1, "Do ##class(Graph.KG.VarLength).EndsInto("_tVs_", $List("_tVL_", 3), "_tDir_", $List("_tVL_", 1), $List("_tVL_", 2), 0, pGName, ."_tEn_")")
	Do ..Add(.pL, tT, "}")
	Do ..Add(.pL, tT, "If $Get(pQ(""vlcount"")) && (pQ(""split"") = """") {")
	Do ..Add(.pL, tT + 1, "Set ^||IVG.CypherEndsCount = $Get(^||IVG.CypherEndsCount) + 1")
	// the end is never bound before its hop (the count is of a lone hop), so Gen's own Unchecked decides it here
	If ..Unchecked(.pQ, tTo) {
		Do ..Add(.pL, tT + 1, "Set c"_pK_" = "_tEn)
	} Else {
		// each end's checks inline, as a walked end's are (IC3: 5,000 ends, a label and a <> each)
		Do ..Add(.pL, tT + 1, "Set c"_pK_" = 0")
		Do ..Add(.pL, tT + 1, "For "_tVi_" = 1:1:"_tEn_" {")
		Do ..Add(.pL, tT + 2, "Set "_tVm_" = $List("_tEn_"("_tVi_"), 1)")
		Do ..Check(.pQ, tTo, tVm, tT + 2, .pL)
		Do ..Add(.pL, tT + 2, "Set c"_pK_" = c"_pK_" + 1")
		Do ..Add(.pL, tT + 1, "}")
	}
	Do ..Add(.pL, tT + 1, "Do:c"_pK_">0 ..GroupCount(.pQ, c"_pK_")")
	Do ..Add(.pL, tT, "} Else {")
	Do ..Add(.pL, tT + 1, "For "_tVi_" = 1:1:"_tEn_" {")
	Set tT = tT + 2
	Do ..Add(.pL, tT, "Quit:tMF&&(pQ(""count"")>=tLim)")
	// a worker walks its share of the ends; every worker computes them in the same order
	Do ..Add(.pL, tT, "Continue:(pQ(""split"")=""vl"")&&((("_tVi_"-1)#tParts)'=tPart)")
	Do ..Add(.pL, tT, "Set "_tVm_" = $List("_tEn_"("_tVi_"), 1)")
	Do ..Check(.pQ, tTo, tVm, tT, .pL)
	Do ..Add(.pL, tT, "Set pB("_tTo_") = "_tVm)
	Do ..Hop(.pQ, pK + 1, .pBound, tT, .pL)
	Do ..Add(.pL, tT, "Kill pB("_tTo_")")
	Do ..Add(.pL, tT - 1, "}")
	Do ..Add(.pL, tT - 2, "}")
}

/// Node pI's checks on the neighbour in variable pM, inline: a failing node continues the neighbour loop. A node with
/// a cross filter is checked by Exec.NodeOK, which reads the other node's binding.
ClassMethod Check(ByRef pQ, pI As %Integer, pM As %String, pT As %Integer, ByRef pL) [ Private ]
{
	Set tIdx = ^||IVG.CypherProps("index"), tData = ^||IVG.CypherProps("data"), tSlot = ^||IVG.CypherProps("slot")
	If $Get(pQ("n", pI, "x")) '= "" {
		Do ..Add(.pL, pT, "Continue:'..NodeOK(.pQ,"_pI_",pKey,pGName,"_pM_",.pB)")
		Quit
	}
	Do:$Data(pQ("n",pI,"id")) ..Add(.pL, pT, "Continue:"_pM_"'=id"_pI)
	Set tL = pQ("n", pI, "labels")
	For tJ = 1:1:$ListLength(tL) {
		Do ..Add(.pL, pT, "Continue:'$Data(^KG(""label"",pKey,"_..Lit($List(tL, tJ))_","_pM_"))")
	}
	Set tP = pQ("n", pI, "props")
	For tJ = 1:1:$ListLength(tP) {
		Do ..Add(.pL, pT, "Set r = $Order("_..Ref(tIdx, "gk, "_pM_", "_..Lit($List($List(tP, tJ), 1))_", """"")_")")
		Do ..Add(.pL, pT, "Continue:r=""""")
		Do ..Add(.pL, pT, "Continue:$ListGet($Get("_..Ref(tData, "r")_"),"_tSlot_")'=pv"_pI_"x"_tJ)
	}
	// a comparison with a missing property is null, which filters the row out; a range orders by sorts-after (]])
	Set tX = pQ("n", pI, "cmp")
	For tJ = 1:1:$ListLength(tX) {
		Set tC = $List(tX, tJ), tVv = "v"_pI_"x"_tJ, tVa = "ca"_pI_"x"_tJ
		Do ..Add(.pL, pT, "Set r = $Order("_..Ref(tIdx, "gk, "_pM_", "_..Lit($List(tC, 1))_", """"")_")")
		Do ..Add(.pL, pT, "Continue:r=""""")
		Do ..Add(.pL, pT, "Set "_tVv_" = $ListGet($Get("_..Ref(tData, "r")_"), "_tSlot_")")
		Set tOp = $List(tC, 2)
		Set tHolds = $Case(tOp, "=": tVv_"="_tVa, "<>": tVv_"'="_tVa, "<": tVa_"]]"_tVv, ">": tVv_"]]"_tVa, "<=": "'("_tVv_"]]"_tVa_")", : "'("_tVa_"]]"_tVv_")")
		Do ..Add(.pL, pT, "Continue:'("_tHolds_")")
	}
}

/// One complete match, of weight pW (an expression): Exec.Emit's work, with Group's inline.
ClassMethod Emission(ByRef pQ, pW As %String, pT As %Integer, ByRef pL) [ Private ]
{
	If pQ("acc") {
		// every match of the anchor falls in one group: summed, grouped once by RunA
		Do ..Add(.pL, pT, "Set pQ(""accw"") = pQ(""accw"") + "_pW)
		Quit
	}
	If 'pQ("agg") {
		If pW = "1" {
			Do ..Add(.pL, pT, "Do ..Emit(.pQ, pGName, .pB)")
		} Else {
			Do ..Add(.pL, pT, "For i = 1:1:"_pW_" { Do ..Emit(.pQ, pGName, .pB) }")
		}
		Quit
	}
	Set tIdx = ^||IVG.CypherProps("index"), tData = ^||IVG.CypherProps("data"), tSlot = ^||IVG.CypherProps("slot")
	// the group: by the nodes its keys read (pQ("cgc"), for the run), else by the keys' values as GroupOf finds it
	// (KeyValues' $List, its properties read inline), and made as GroupOf makes it
	If pQ("ret") = "" {
		Do ..Add(.pL, pT, "Set g = $Get(pQ(""g0""))")
		Do ..Add(.pL, pT, "Set:g="""" g = ..GroupOf(.pQ, pGName, .pB)")
	} Else {
		Set tSub = "gk", tVars = pQ("kvars")
		For tJ = 1:1:$ListLength(tVars) { Set tSub = tSub_", pB("_$List(tVars, tJ)_")" }
		Do ..Add(.pL, pT, "Set g = $Get(pQ(""cgc"", "_tSub_"))")
		Do ..Add(.pL, pT, "If g = """" {")
		Do ..Add(.pL, pT + 1, "Set kv = """"")
		Set tRet = pQ("ret")
		For tJ = 1:1:$ListLength(tRet) {
			Set tItem = $List(tRet, tJ), tVar = $List(tItem, 1), tProp = $List(tItem, 2)
			If tProp = "__node" {
				Do ..Add(.pL, pT + 1, "Set kv = kv_$ListBuild($ListBuild(pGName, pB("_tVar_")))")
				Continue
			}
			Do ..Add(.pL, pT + 1, "Set r = $Order("_..Ref(tIdx, "gk, pB("_tVar_"), "_..Lit(tProp)_", """"")_")")
			Set tVal = "$ListGet($Get("_..Ref(tData, "r")_"), "_tSlot_")"
			// a substring is a string, whatever it looks like
			Set:$ListGet(tItem,3)'="" tVal = """s""_..Substring("_tVal_", $List($List(pQ(""ret""), "_tJ_"), 3))"
			Do ..Add(.pL, pT + 1, "Set kv = kv_$Select(r = """": $ListBuild(), 1: $ListBuild("_tVal_"))")
		}
		Do ..Add(.pL, pT + 1, "If $Length(kv) < 400 {")
		Do ..Add(.pL, pT + 2, "Set g = $Get(^||IVG.CypherRun(""grpv"", kv))")
		Do ..Add(.pL, pT + 2, "If g = """" { Set g = $Increment(^||IVG.CypherRun(""g"")), ^||IVG.CypherRun(""g"", g) = $Char(1)_kv, ^||IVG.CypherRun(""grpv"", kv) = g }")
		Do ..Add(.pL, pT + 1, "} Else {")
		Do ..Add(.pL, pT + 2, "Set g = ..GroupOf(.pQ, pGName, .pB)")
		Do ..Add(.pL, pT + 1, "}")
		Do ..Add(.pL, pT + 1, "Set pQ(""cgc"", "_tSub_") = g")
		Do ..Add(.pL, pT, "}")
	}
	// count(DISTINCT) members in the run's arrays (pQ("cgd")) until Exec.SpillDistinct moves them to Group's own
	Set tAggs = pQ("aggs")
	For tJ = 1:1:$ListLength(tAggs) {
		Set tA = $List(tAggs, tJ)
		If $List(tA, 1) = "count_distinct" {
			Set tN = "pB("_$List(tA, 2)_")"
			Do ..Add(.pL, pT, "If pQ(""cgds"") {")
			Do ..Add(.pL, pT + 1, "Set nd = $ListBuild(pGName, "_tN_")")
			Do ..Add(.pL, pT + 1, "If '$Data(^||IVG.CypherRun(""gd"", g, "_tJ_", nd)) { Set ^||IVG.CypherRun(""gd"", g, "_tJ_", nd) = """" Do $Increment(^||IVG.CypherRun(""g"", g, "_tJ_")) }")
			Do ..Add(.pL, pT, "} ElseIf '$Data(pQ(""cgd"", g, "_tJ_", gk, "_tN_")) {")
			Do ..Add(.pL, pT + 1, "Set pQ(""cgd"", g, "_tJ_", gk, "_tN_") = """"")
			Do ..Add(.pL, pT + 1, "Do $Increment(^||IVG.CypherRun(""g"", g, "_tJ_"))")
			Do ..Add(.pL, pT + 1, "Do:$Increment(pQ(""cgdn""))>=200000 ..SpillDistinct(.pQ)")
			Do ..Add(.pL, pT, "}")
		} Else {
			Do ..Add(.pL, pT, "Do $Increment(^||IVG.CypherRun(""g"", g, "_tJ_"), "_pW_")")
		}
	}
}

/// Whether node pI is checked for nothing: Exec.Unchecked for a node not bound before its hop (keep the two in step).
ClassMethod Unchecked(ByRef pQ, pI As %Integer) As %Boolean [ Private ]
{
	Quit:$Data(pQ("n",pI,"id")) 0
	Quit (pQ("n", pI, "labels") = "") && (pQ("n", pI, "props") = "") && (pQ("n", pI, "cmp") = "") && ($Get(pQ("n", pI, "x")) = "")
}

/// Global reference pGlobal (a name, or a name with subscripts) with subscripts pSubs after its own.
ClassMethod Ref(pGlobal As %String, pSubs As %String) As %String [ Private ]
{
	Quit:$Extract(pGlobal,*)=")" $Extract(pGlobal, 1, *-1)_", "_pSubs_")"
	Quit pGlobal_"("_pSubs_")"
}

/// String pS as an ObjectScript literal: quoted, or as $Char() where it holds a control character.
ClassMethod Lit(pS As %String) As %String [ Private ]
{
	If $ZStrip(pS, "*C") = pS {
		Quit """"_$Replace(pS, """", """""")_""""
	}
	Set tOut = ""
	For tI = 1:1:$Length(pS) { Set tOut = tOut_$Select(tI = 1: "", 1: ",")_$Ascii(pS, tI) }
	Quit "$Char("_tOut_")"
}

/// The strings of $List pList as comma-separated literals.
ClassMethod Lits(pList As %List) As %String [ Private ]
{
	Set tOut = ""
	For tI = 1:1:$ListLength(pList) { Set tOut = tOut_$Select(tI = 1: "", 1: ", ")_..Lit($List(pList, tI)) }
	Quit tOut
}

ClassMethod Add(ByRef pL, pT As %Integer, pLine As %String) [ Private ]
{
	Set pL($Increment(pL)) = $Translate($Justify("", pT), " ", $Char(9))_pLine
}

}
