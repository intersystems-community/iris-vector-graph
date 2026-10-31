/// Test-only oracle for the ^NKG rewrite (spec 260 D-NKG-EQUIV, D-NKG-SWAP). Loaded by
/// tests/e2e/test_nkg_live_rewrite_e2e.py into the test namespace; never shipped in iris_src.
///
/// Snap decodes an ^NKG-shaped global to NAMES through $ND and $LS and never compares a raw
/// index, because index assignment depends on scan order and on the incremental writers.
/// Snapshots live in ^IVGQ(tag, category, key); keys are $ListBuild of names.
///
/// Two places where a by-name value comparison cannot hold once indices are stable across
/// rebuilds, and what is compared instead (told to the lead before building):
///   - $agg: the HyperLogLog sketch hashes the target INDEX (GraphIndex.UpdateStructuralHLL),
///     so two builds that intern differently hold different sketches for the same names.
///     The key set is compared by names; each side's sketch must equal the sketch recomputed
///     from that side's own -1 rows ("aggself"); the values are compared by name only when
///     pStrict = 1 (both sides interned identically).
///   - $meta nodeCount / labelCount are interning counters (the next index), never lowered,
///     so after a removal they exceed a fresh build's. Compared by rule (each side >= 1 + its
///     highest index) unless pStrict = 1. edgeCount is compared by value. version and
///     builtFrom are compared by rule (present on both sides when present on one).
Class IVGTest.NkgEquiv Extends %RegisteredObject
{

/// Snapshot the ^NKG-shaped global pGlo (default ^NKG) into ^IVGQ(pTag). Returns the -1 row count.
ClassMethod Snap(pTag As %String, pGlo As %String = "^NKG") As %Integer
{
	Kill ^IVGQ(pTag)
	Set tBad = 0, tRows = 0, tMaxN = -1, tMaxL = -1
	Set tI = ""
	For {
		Set tI = $Order(@pGlo@("$ND", tI), 1, tName)
		Quit:tI=""
		Set ^IVGQ(pTag, "nodes", $ListBuild(tName)) = tI
		Set:tI>tMaxN tMaxN = tI
		If $Get(@pGlo@("$NI", tName), -1) '= tI {
			Set tBad = tBad + 1, ^IVGQ(pTag, "bad", tBad) = "ND/NI round trip: "_tI_" -> "_$Extract(tName, 1, 40)
		}
	}
	Set tName = ""
	For {
		Set tName = $Order(@pGlo@("$NI", tName), 1, tI)
		Quit:tName=""
		If $Get(@pGlo@("$ND", tI), $Char(0)) '= tName {
			Set tBad = tBad + 1, ^IVGQ(pTag, "bad", tBad) = "NI orphan: "_$Extract(tName, 1, 40)_" -> "_tI
		}
	}
	Set tI = ""
	For {
		Set tI = $Order(@pGlo@("$LS", tI), 1, tName)
		Quit:tI=""
		Set ^IVGQ(pTag, "labels", $ListBuild(tName)) = tI
		Set:tI>tMaxL tMaxL = tI
		If $Get(@pGlo@("$LI", tName), -1) '= tI {
			Set tBad = tBad + 1, ^IVGQ(pTag, "bad", tBad) = "LS/LI round trip: "_tI_" -> "_tName
		}
	}
	Set tName = ""
	For {
		Set tName = $Order(@pGlo@("$LI", tName), 1, tI)
		Quit:tName=""
		If $Get(@pGlo@("$LS", tI), $Char(0)) '= tName {
			Set tBad = tBad + 1, ^IVGQ(pTag, "bad", tBad) = "LI orphan: "_tName_" -> "_tI
		}
	}
	For tR = 0, 1, 2 {
		If $Get(@pGlo@("$LS", tR)) '= $ListGet($ListBuild("out", "in", "deg"), tR + 1) {
			Set tBad = tBad + 1, ^IVGQ(pTag, "bad", tBad) = "reserved label "_tR_" is "_$Get(@pGlo@("$LS", tR))
		}
	}
	Set ^IVGQ(pTag, "max", "node") = tMaxN, ^IVGQ(pTag, "max", "label") = tMaxL
	// -1 and -2 rows by names; the middle subscript is -(labelIdx+1)
	For tDir = -1, -2 {
		Set tCat = "e"_(-tDir)
		Set tS = ""
		For {
			Set tS = $Order(@pGlo@(tDir, tS))
			Quit:tS=""
			Set tP = ""
			For {
				Set tP = $Order(@pGlo@(tDir, tS, tP))
				Quit:tP=""
				Set tO = ""
				For {
					Set tO = $Order(@pGlo@(tDir, tS, tP, tO), 1, tW)
					Quit:tO=""
					Set tSN = $Get(@pGlo@("$ND", tS), $Char(0)), tPN = $Get(@pGlo@("$LS", -tP - 1), $Char(0)), tON = $Get(@pGlo@("$ND", tO), $Char(0))
					If (tSN = $Char(0)) || (tPN = $Char(0)) || (tON = $Char(0)) {
						Set tBad = tBad + 1, ^IVGQ(pTag, "bad", tBad) = tCat_" row with an undecodable index: "_tS_","_tP_","_tO
						Continue
					}
					Set ^IVGQ(pTag, tCat, $ListBuild(tSN, tPN, tON)) = tW
					If tDir = -1 {
						Set tRows = tRows + 1
						If $Get(@pGlo@(-2, tO, tP, tS), $Char(0)) '= tW {
							Set tBad = tBad + 1, ^IVGQ(pTag, "bad", tBad) = "-1 row without its -2 mirror: "_tS_","_tP_","_tO
						}
					} ElseIf '$Data(@pGlo@(-1, tO, tP, tS)) {
						Set tBad = tBad + 1, ^IVGQ(pTag, "bad", tBad) = "-2 row without its -1 row: "_tS_","_tP_","_tO
					}
				}
			}
		}
	}
	// -3: the merged out-degree per node name
	Set tS = ""
	For {
		Set tS = $Order(@pGlo@(-3, tS), 1, tD)
		Quit:tS=""
		Set ^IVGQ(pTag, "d", $ListBuild($Get(@pGlo@("$ND", tS), "?"_tS))) = tD
	}
	Set tK = ""
	For {
		Set tK = $Order(@pGlo@("$meta", tK), 1, tV)
		Quit:tK=""
		Set ^IVGQ(pTag, "meta", $ListBuild(tK)) = tV
	}
	// $agg by names, and each sketch checked against the sketch of the same side's -1 rows
	Set tSelf = 0
	Set tS = ""
	For {
		Set tS = $Order(@pGlo@("$agg", tS))
		Quit:tS=""
		Set tP = ""
		For {
			Set tP = $Order(@pGlo@("$agg", tS, tP))
			Quit:tP=""
			Set tH = $Get(@pGlo@("$agg", tS, tP, "hll"))
			Set ^IVGQ(pTag, "agg", $ListBuild($Get(@pGlo@("$ND", tS), "?"_tS), $Get(@pGlo@("$LS", tP), "?"_tP))) = tH
			If tH '= ..SketchOfRows(pGlo, tS, tP) {
				Set tSelf = tSelf + 1
				Set tBad = tBad + 1, ^IVGQ(pTag, "bad", tBad) = "$agg differs from the sketch of its own rows: "_tS_","_tP
			}
		}
	}
	// every (source, label) with rows has a sketch
	Set tS = ""
	For {
		Set tS = $Order(@pGlo@(-1, tS))
		Quit:tS=""
		Set tP = ""
		For {
			Set tP = $Order(@pGlo@(-1, tS, tP))
			Quit:tP=""
			If '$Data(@pGlo@("$agg", tS, -tP - 1, "hll")) {
				Set tBad = tBad + 1, ^IVGQ(pTag, "bad", tBad) = "rows without a sketch: "_tS_","_tP
			}
		}
	}
	Set ^IVGQ(pTag, "aggself") = tSelf
	Quit tRows
}

/// The HLL sketch of the targets of rows (pS, label pP) of pGlo, by UpdateStructuralHLL's algorithm.
ClassMethod SketchOfRows(pGlo As %String, pS, pP) As %List
{
	Set tH = ##class(Graph.KG.GraphIndex).EmptyHLL(), tAny = 0
	Set tO = ""
	For {
		Set tO = $Order(@pGlo@(-1, pS, -pP - 1, tO))
		Quit:tO=""
		Set tAny = 1
		Do ..HLLAdd(.tH, tO)
	}
	Quit $Select(tAny: tH, 1: "")
}

/// GraphIndex.UpdateStructuralHLL on a local sketch.
ClassMethod HLLAdd(ByRef pH As %List, pIdx)
{
	Set tHash = $SYSTEM.Encryption.SHA1Hash(pIdx _ "")
	Set tReg = ($ASCII(tHash, 1) # 256) + 1
	Set tB = $ASCII(tHash, 2), tLz = 1
	If tB = 0 {
		Set tLz = 9
	} Else {
		While (tB # 2) = 0 { Set tLz = tLz + 1, tB = tB \ 2 }
	}
	Set:tLz>$List(pH, tReg) $List(pH, tReg) = tLz
}

/// Count differences between snapshots pA and pB per category: same, only in A, only in B,
/// differing value (weights numerically within 1e-9). See the class comment for pStrict.
ClassMethod Diff(pA As %String, pB As %String, pStrict As %Boolean = 0) As %String
{
	Set tOut = {}
	For tCat = "nodes", "labels", "e1", "e2", "d", "meta", "agg" {
		Set (tOnlyA, tOnlyB, tDiff, tSame) = 0, tEx = ""
		Set tK = ""
		For {
			Set tK = $Order(^IVGQ(pA, tCat, tK), 1, tVA)
			Quit:tK=""
			Continue:(tCat="meta")&&..ByRule(tK)
			If '$Data(^IVGQ(pB, tCat, tK), tVB) {
				Set tOnlyA = tOnlyA + 1
				Set:tEx="" tEx = "onlyA:"_..Show(tK)
				Continue
			}
			If ..Equal(pA, pB, tCat, tK, tVA, tVB, pStrict) {
				Set tSame = tSame + 1
			} Else {
				Set tDiff = tDiff + 1
				Set:tEx="" tEx = "differ:"_..Show(tK)_" "_$Extract(tVA, 1, 24)_" vs "_$Extract(tVB, 1, 24)
			}
		}
		Set tK = ""
		For {
			Set tK = $Order(^IVGQ(pB, tCat, tK))
			Quit:tK=""
			Continue:(tCat="meta")&&..ByRule(tK)
			If '$Data(^IVGQ(pA, tCat, tK)) {
				Set tOnlyB = tOnlyB + 1
				Set:tEx="" tEx = "onlyB:"_..Show(tK)
			}
		}
		Do tOut.%Set(tCat, {"same": (tSame), "only_a": (tOnlyA), "only_b": (tOnlyB), "differ": (tDiff), "example": (tEx)})
	}
	For tSide = pA, pB {
		Set tN = 0, tEx = ""
		Set tK = ""
		For {
			Set tK = $Order(^IVGQ(tSide, "bad", tK), 1, tV)
			Quit:tK=""
			Set tN = tN + 1
			Set:tEx="" tEx = tV
		}
		Do tOut.%Set("bad_"_$Select(tSide = pA: "a", 1: "b"), {"count": (tN), "example": (tEx)})
	}
	Quit tOut.%ToJSON()
}

/// The $meta keys compared by rule, not by presence or value: version (a counter, checked by
/// the tests before and after each rebuild), builtFrom (the token the last rewrite started
/// from; the legacy reference build has none) and rebuild (the mark of a rewrite in progress).
ClassMethod ByRule(pK) As %Boolean [ Private ]
{
	Quit $ListFind($ListBuild("version", "builtFrom", "rebuild", "dictSwept", "liveNodes", "liveLabels"), $ListGet(pK)) > 0
}

ClassMethod Equal(pA, pB, pCat, pK, pVA, pVB, pStrict) As %Boolean [ Private ]
{
	If pVA = pVB Quit 1
	If pCat = "agg" Quit 'pStrict
	If pCat = "meta" {
		Set tKey = $ListGet(pK)
		If (tKey = "version") || (tKey = "builtFrom") || (tKey = "rebuild") Quit 1
		If 'pStrict {
			If tKey = "nodeCount" Quit (pVA > $Get(^IVGQ(pA, "max", "node"))) && (pVB > $Get(^IVGQ(pB, "max", "node")))
			If tKey = "labelCount" Quit (pVA > $Get(^IVGQ(pA, "max", "label"))) && (pVB > $Get(^IVGQ(pB, "max", "label")))
		}
		Quit 0
	}
	If (pCat = "nodes") || (pCat = "labels") Quit 'pStrict
	If $IsValidNum(pVA) && $IsValidNum(pVB) && ($ZABS(pVA - pVB) < 0.000000001) Quit 1
	Quit 0
}

ClassMethod Show(pK) As %String [ Private ]
{
	Set tOut = ""
	For tI = 1:1:$ListLength(pK) { Set tOut = tOut _ $Select(tI > 1: "/", 1: "") _ $Extract($ListGet(pK, tI), 1, 40) }
	Quit tOut
}

/// Keys of category pCat in snapshot pX that are in neither pA nor pB (an interrupted rewrite
/// may hold a mix of the two graphs, never anything else). Returns "count example".
ClassMethod Outside(pX As %String, pA As %String, pB As %String, pCat As %String) As %String
{
	Set tN = 0, tEx = ""
	Set tK = ""
	For {
		Set tK = $Order(^IVGQ(pX, pCat, tK))
		Quit:tK=""
		Continue:$Data(^IVGQ(pA, pCat, tK))||$Data(^IVGQ(pB, pCat, tK))
		Set tN = tN + 1
		Set:tEx="" tEx = ..Show(tK)
	}
	Quit tN_" "_tEx
}

/// The structural faults of snapshot pTag, leaving out the sketch checks when pNoSketch = 1
/// (an interrupted rewrite has not recomputed every sketch yet). Returns "count example".
ClassMethod Faults(pTag As %String, pNoSketch As %Boolean = 0) As %String
{
	Set tN = 0, tEx = ""
	Set tK = ""
	For {
		Set tK = $Order(^IVGQ(pTag, "bad", tK), 1, tV)
		Quit:tK=""
		Continue:pNoSketch&&(tV["sketch")
		Set tN = tN + 1
		Set:tEx="" tEx = tV
	}
	Quit tN_" "_tEx
}

/// Names present in both snapshots whose index moved, for nodes and labels.
ClassMethod SameIndex(pA As %String, pB As %String) As %String
{
	Set tOut = {}
	For tCat = "nodes", "labels" {
		Set (tBoth, tMoved) = 0, tEx = ""
		Set tK = ""
		For {
			Set tK = $Order(^IVGQ(pA, tCat, tK), 1, tIA)
			Quit:tK=""
			Continue:'$Data(^IVGQ(pB, tCat, tK), tIB)
			Set tBoth = tBoth + 1
			If tIA '= tIB {
				Set tMoved = tMoved + 1
				Set:tEx="" tEx = ..Show(tK)_" "_tIA_" -> "_tIB
			}
		}
		Do tOut.%Set(tCat, {"both": (tBoth), "moved": (tMoved), "example": (tEx)})
	}
	Quit tOut.%ToJSON()
}

/// The pre-fix builder, kept here as the reference: a from-scratch build of the merged
/// ^KG("out") view into pGlo (not ^NKG), with its own interning in scan order, the same
/// sketches and the same merged degree. A copy of TraversalBuild.BuildNKG before D-NKG-SWAP.
ClassMethod LegacyBuild(pGlo As %String = "^IVGQR") As %Integer
{
	Kill @pGlo
	Set @pGlo@("$LS", 0) = "out", @pGlo@("$LI", "out") = 0
	Set @pGlo@("$LS", 1) = "in", @pGlo@("$LI", "in") = 1
	Set @pGlo@("$LS", 2) = "deg", @pGlo@("$LI", "deg") = 2
	Set @pGlo@("$meta", "labelCount") = 3
	Set tEdges = 0
	Set g = ""
	For {
		Set g = $Order(^KG("out", g))
		Quit:g=""
		Set tWarm = ##class(Graph.KG.Adj).AllWarm(g)
		Set src = ""
		For {
			Set src = $Order(^KG("out", g, src))
			Quit:src=""
			Set sIdx = ..RefIntern(pGlo, src)
			Set pred = ""
			For {
				Set pred = $Order(^KG("out", g, src, pred))
				Quit:pred=""
				Set pIdx = $Get(@pGlo@("$LI", pred))
				If pIdx = "" {
					Set pIdx = $Increment(@pGlo@("$meta", "labelCount")) - 1
					Set @pGlo@("$LS", pIdx) = pred, @pGlo@("$LI", pred) = pIdx
				}
				Set dst = ""
				For {
					Set dst = $Order(^KG("out", g, src, pred, dst), 1, weight)
					Quit:dst=""
					Set oIdx = ..RefIntern(pGlo, dst)
					If '$Data(@pGlo@(-1, sIdx, -(pIdx + 1), oIdx)) {
						Set @pGlo@(-1, sIdx, -(pIdx + 1), oIdx) = weight
						Set @pGlo@(-2, oIdx, -(pIdx + 1), sIdx) = weight
						Set tEdges = tEdges + 1
					}
					Set tH = $Get(@pGlo@("$agg", sIdx, pIdx, "hll"), ##class(Graph.KG.GraphIndex).EmptyHLL())
					Do ..HLLAdd(.tH, oIdx)
					Set @pGlo@("$agg", sIdx, pIdx, "hll") = tH
				}
			}
			If tWarm {
				Set tDeg = $ListGet($Get(^KG("degc", g, src, $Char(1))), 2, 0)
			} Else {
				Set tDeg = ##class(Graph.KG.Adj).Degree(g, src)
			}
			Set @pGlo@(-3, sIdx) = $Get(@pGlo@(-3, sIdx), 0) + tDeg
		}
	}
	Set @pGlo@("$meta", "edgeCount") = tEdges
	Set @pGlo@("$meta", "version") = 1
	Quit tEdges
}

ClassMethod RefIntern(pGlo As %String, pName As %String) As %Integer [ Private ]
{
	Set tI = $Get(@pGlo@("$NI", pName))
	Quit:tI'="" tI
	Set tI = $Increment(@pGlo@("$meta", "nodeCount")) - 1
	Set @pGlo@("$ND", tI) = pName, @pGlo@("$NI", pName) = tI
	Quit tI
}

/// The fixture graph, written through Graph.KG.Adjacency (the one writer of ^KG). Node names
/// are pPrefix_i. The default graph holds pNodes sources with about pDeg out-edges each over
/// three predicates; named graphs pPrefix_"g1" and pPrefix_"g2" hold a third of the nodes
/// each, with the same names (shared nodes), predicates P1 and P4, and a few triples that
/// also exist in the default graph with another weight (parallel edges across graphs).
/// Extra cases: an edge written with no weight, numeric-looking names (pPrefix is prepended
/// to every name except these: "42", "007", "3.5"), and a 256-character name (the longest a
/// node id can be, rdf_edges.s is VARCHAR(256)). Returns the number of Put calls.
ClassMethod Gen(pPrefix As %String, pNodes As %Integer, pDeg As %Integer, pSeed As %Integer = 7) As %Integer
{
	Set tN = 0, tR = pSeed
	Set tG1 = ##class(Graph.KG.GraphKey).ForIndex(pPrefix_"g1"), tG2 = ##class(Graph.KG.GraphKey).ForIndex(pPrefix_"g2")
	For tS = 1:1:pNodes {
		For tE = 1:1:pDeg {
			Set tR = (tR * 1103515245 + 12345) # 2147483648
			Set tO = (tR # pNodes) + 1, tP = "P"_((tR \ 7) # 3 + 1), tW = ((tR \ 11) # 2900 + 100) / 1000
			$$$ThrowOnError(##class(Graph.KG.Adjacency).Put(0, pPrefix_tS, tP, pPrefix_tO, tW))
			Set tN = tN + 1
		}
	}
	For tS = 1:1:(pNodes \ 3) {
		For tE = 1:1:3 {
			Set tR = (tR * 1103515245 + 12345) # 2147483648
			Set tO = (tR # pNodes) + 1, tP = $Select(tE = 1: "P1", 1: "P4"), tW = ((tR \ 11) # 2900 + 100) / 1000
			$$$ThrowOnError(##class(Graph.KG.Adjacency).Put($Select(tS # 2: tG1, 1: tG2), pPrefix_tS, tP, pPrefix_tO, tW))
			Set tN = tN + 1
		}
	}
	// parallel edges across graphs: the same triple in the default graph and in g1, weights differ
	For tS = 1:1:20 {
		Set tP = $Order(^KG("out", 0, pPrefix_tS, ""))
		Continue:tP=""
		Set tO = $Order(^KG("out", 0, pPrefix_tS, tP, ""), 1, tW)
		$$$ThrowOnError(##class(Graph.KG.Adjacency).Put(tG1, pPrefix_tS, tP, tO, tW + 5))
		Set tN = tN + 1
	}
	// no weight, numeric-looking names, a 256-character name
	Set tLong = pPrefix_$Translate($Justify("", 256 - $Length(pPrefix)), " ", "L")
	$$$ThrowOnError(##class(Graph.KG.Adjacency).Put(0, pPrefix_"1", "P2", pPrefix_"noweight", ""))
	$$$ThrowOnError(##class(Graph.KG.Adjacency).Put(0, "42", "P1", "007", 2.5))
	$$$ThrowOnError(##class(Graph.KG.Adjacency).Put(tG2, "007", "P4", "3.5", 1))
	$$$ThrowOnError(##class(Graph.KG.Adjacency).Put(0, pPrefix_"2", "P3", "42", 1))
	$$$ThrowOnError(##class(Graph.KG.Adjacency).Put(0, tLong, "P1", pPrefix_"3", 1.25))
	$$$ThrowOnError(##class(Graph.KG.Adjacency).Put(tG1, pPrefix_"4", "P1", tLong, 0.75))
	Quit tN + 6
}

/// Remove every fixture edge (sources named pPrefix_*, "42", "007", the long name) from every
/// graph through Graph.KG.Adjacency, so the namespace keeps no ^KG rows without SQL rows.
ClassMethod Clean(pPrefix As %String) As %Integer
{
	Set tN = 0
	Set g = ""
	For {
		Set g = $Order(^KG("out", g))
		Quit:g=""
		For tFixed = "42", "007" {
			If $Data(^KG("out", g, tFixed)) { Do ##class(Graph.KG.Adjacency).RemoveSource(g, tFixed)  Set tN = tN + 1 }
		}
		Set src = pPrefix
		For {
			Set src = $Order(^KG("out", g, src))
			Quit:(src="")||($Extract(src, 1, $Length(pPrefix))'=pPrefix)
			Do ##class(Graph.KG.Adjacency).RemoveSource(g, src)
			Set tN = tN + 1
		}
	}
	Quit tN
}

/// Make ^NKG disagree with ^KG before a rebuild, the way real drift arises:
///  - pAdd edges written through Adjacency.PutShadow (in ^KG, not interned: what a temporal
///    shadow does), half of them to new names pPrefix_"new"_i;
///  - pStale rows injected into ^NKG with no ^KG edge (a Bypass Write; what a %NOINDEX row
///    delete or an older release leaves), with their sketches, degrees and two dictionary
///    names that exist nowhere in ^KG (pPrefix_"ghost1", pPrefix_"ghost2") and one label
///    ("PGHOST") that no edge uses.
/// Test-only: ^NKG is written directly here on purpose.
ClassMethod Drift(pPrefix As %String, pNodes As %Integer, pAdd As %Integer, pStale As %Integer) As %Integer
{
	Set tR = 99
	For tI = 1:1:pAdd {
		Set tR = (tR * 1103515245 + 12345) # 2147483648
		Set tS = (tR # pNodes) + 1
		Set tO = $Select(tI # 2: pPrefix_"new"_tI, 1: pPrefix_((tR \ 13) # pNodes + 1))
		$$$ThrowOnError(##class(Graph.KG.Adjacency).PutShadow(0, pPrefix_tS, "P5", tO, 1.5))
	}
	Set tG1 = ##class(Graph.KG.GraphIndex).InternNode(pPrefix_"ghost1")
	Set tG2 = ##class(Graph.KG.GraphIndex).InternNode(pPrefix_"ghost2")
	Set tPG = ##class(Graph.KG.GraphIndex).InternLabel("PGHOST")
	Set tP1 = ##class(Graph.KG.GraphIndex).InternLabel("P1")
	For tI = 1:1:pStale {
		Set tR = (tR * 1103515245 + 12345) # 2147483648
		Set tS = $Get(^NKG("$NI", pPrefix_((tR # pNodes) + 1)))
		Set tO = $Get(^NKG("$NI", pPrefix_(((tR \ 17) # pNodes) + 1)))
		Continue:(tS="")||(tO="")
		Set tPk = $Select(tI # 5 = 0: -(tPG + 1), 1: -(tP1 + 1))
		If tI # 7 = 0 Set tO = tG1
		If tI # 11 = 0 Set tS = tG2
		Continue:$Data(^NKG(-1, tS, tPk, tO))
		Set ^NKG(-1, tS, tPk, tO) = 9.99, ^NKG(-2, tO, tPk, tS) = 9.99
		Set tmp = $Increment(^NKG(-3, tS))
		Do ##class(Graph.KG.GraphIndex).UpdateStructuralHLL(tS, -tPk - 1, tO)
	}
	Quit pAdd + pStale
}

// ---- jobs: reader, builder, writer -------------------------------------------------

/// Prepare the reader's expectations for run pRun from two snapshots: pBefore (^NKG before the
/// rebuild) and pAfter (the reference build of the graph after). For each sampled name in
/// ^IVGQ(pRun, "sample", name): it must be interned when it is in both; its -1 rows must
/// include every (label, target) of both and be drawn from those of either; its -3 degree
/// must lie between the two.
ClassMethod Expect(pRun As %String, pBefore As %String, pAfter As %String) As %Integer
{
	Set tN = 0
	Set tName = ""
	For {
		Set tName = $Order(^IVGQ(pRun, "sample", tName))
		Quit:tName=""
		Set tN = tN + 1
		Kill ^IVGQ(pRun, "may", tName), ^IVGQ(pRun, "must", tName)
		Set tInB = $Data(^IVGQ(pBefore, "nodes", $ListBuild(tName))), tInA = $Data(^IVGQ(pAfter, "nodes", $ListBuild(tName)))
		For tSide = pBefore, pAfter {
			Set tK = $ListBuild(tName)
			For {
				Set tK = $Order(^IVGQ(tSide, "e1", tK))
				Quit:(tK="")||($ListGet(tK, 1)'=tName)
				Set tPO = $ListBuild($ListGet(tK, 2), $ListGet(tK, 3))
				Set ^IVGQ(pRun, "may", tName, tPO) = ""
				If (tSide = pBefore) && $Data(^IVGQ(pAfter, "e1", tK)) Set ^IVGQ(pRun, "must", tName, tPO) = ""
			}
		}
		Set tMust = 0, tK = ""
		For { Set tK = $Order(^IVGQ(pRun, "must", tName, tK))  Quit:tK=""  Set tMust = tMust + 1 }
		Set tDB = $Get(^IVGQ(pBefore, "d", $ListBuild(tName)), 0), tDA = $Get(^IVGQ(pAfter, "d", $ListBuild(tName)), 0)
		Set ^IVGQ(pRun, "sample", tName) = $ListBuild((tInB && tInA), $Select(tDB < tDA: tDB, 1: tDA), $Select(tDB > tDA: tDB, 1: tDA), tMust)
	}
	Quit tN
}

/// Choose the reader's sample from snapshot pBefore: the pK names interned first and the pK
/// interned last (late in scan order: the case the old rebuild hid for most of its run), every
/// 97th name between, and the sources of drift (pExtra, comma-separated). Returns the sample size.
ClassMethod Sample(pRun As %String, pBefore As %String, pK As %Integer, pExtra As %String = "") As %Integer
{
	Kill ^IVGQ(pRun, "sample"), ^||byIdx
	Set tK = ""
	For {
		Set tK = $Order(^IVGQ(pBefore, "nodes", tK), 1, tI)
		Quit:tK=""
		Set ^||byIdx(tI) = $ListGet(tK)
	}
	Set (tI, tN) = 0
	Set tI = ""
	For {
		Set tI = $Order(^||byIdx(tI), 1, tName)
		Quit:tI=""
		Set tN = tN + 1
		Set:(tN<=pK)||(tN#97=0) ^IVGQ(pRun, "sample", tName) = ""
	}
	Set tI = "", tC = 0
	For {
		Set tI = $Order(^||byIdx(tI), -1, tName)
		Quit:(tI="")||(tC>=pK)
		Set tC = tC + 1, ^IVGQ(pRun, "sample", tName) = ""
	}
	For tJ = 1:1:$Length(pExtra, ",") {
		Set tX = $Piece(pExtra, ",", tJ)
		Set:tX'="" ^IVGQ(pRun, "sample", tX) = ""
	}
	Kill ^||byIdx
	Set tN = 0, tName = ""
	For { Set tName = $Order(^IVGQ(pRun, "sample", tName))  Quit:tName=""  Set tN = tN + 1 }
	Quit tN
}

/// The reader job: until ^IVGQ(pRun, "stop"), read every sampled name's dictionary entry,
/// rows and degree in ^NKG, as the 18 ^NKG readers do (no lock), and count what it saw.
/// Writes ^IVGQ(pRun, "r") = counts as JSON.
ClassMethod Reader(pRun As %String) As %Status
{
	Set (tReads, tWin, tNoNode, tNoRow, tInvalid, tMissing, tBadDeg, tBadDict, tTorn, tVerBack, tPass) = 0
	Set tEx = "", tVer = +$Get(^NKG("$meta", "version"))
	Set ^IVGQ(pRun, "reader") = $Job
	While '$Get(^IVGQ(pRun, "stop")) {
		Set tPass = tPass + 1, ^IVGQ(pRun, "passes") = tPass
		Set tName = ""
		For {
			Set tName = $Order(^IVGQ(pRun, "sample", tName), 1, tX)
			Quit:tName=""
			Set tReads = tReads + 1
			Set:$Get(^IVGQ(pRun, "building")) tWin = tWin + 1
			Set tV = +$Get(^NKG("$meta", "version"))
			If tV < tVer { Set tVerBack = tVerBack + 1 }
			Set tVer = tV
			Set tIdx = $Get(^NKG("$NI", tName))
			If tIdx = "" {
				If $List(tX, 1) { Set tNoNode = tNoNode + 1  Set:tEx="" tEx = "no node "_tName }
				Continue
			}
			If $Get(^NKG("$ND", tIdx)) '= tName {
				If $Data(^NKG("$NI", tName)) { Set tBadDict = tBadDict + 1  Set:tEx="" tEx = "dict "_tName }
			}
			If '$Data(^NKG(-1, tIdx)) {
				If $List(tX, 4) > 0 { Set tNoRow = tNoRow + 1  Set:tEx="" tEx = "no row "_tName }
				Continue
			}
			Set tSeen = 0
			Set tPk = ""
			For {
				Set tPk = $Order(^NKG(-1, tIdx, tPk))
				Quit:tPk=""
				Set tO = ""
				For {
					Set tO = $Order(^NKG(-1, tIdx, tPk, tO))
					Quit:tO=""
					Set tPO = $ListBuild($Get(^NKG("$LS", -tPk - 1)), $Get(^NKG("$ND", tO)))
					If '$Data(^IVGQ(pRun, "may", tName, tPO)) {
						// a row read while the sweep removed it and then its dictionary entry is a
						// torn read of a stale row, not an invented one
						If '$Data(^NKG(-1, tIdx, tPk, tO)) { Set tTorn = tTorn + 1  Continue }
						Set tInvalid = tInvalid + 1
						Set:tEx="" tEx = "invalid row "_tName_" -> "_$ListGet(tPO, 1)_"/"_$ListGet(tPO, 2)
						Continue
					}
					Set:$Data(^IVGQ(pRun, "must", tName, tPO)) tSeen = tSeen + 1
				}
			}
			If tSeen < $List(tX, 4) { Set tMissing = tMissing + 1  Set:tEx="" tEx = "missing rows "_tName_" "_tSeen_"<"_$List(tX, 4) }
			Set tD = $Get(^NKG(-3, tIdx), 0)
			If (tD < $List(tX, 2)) || (tD > $List(tX, 3)) { Set tBadDeg = tBadDeg + 1  Set:tEx="" tEx = "degree "_tName_" "_tD }
		}
	}
	Set ^IVGQ(pRun, "r") = {"reads": (tReads), "in_window": (tWin), "passes": (tPass), "no_node": (tNoNode), "no_row": (tNoRow), "invalid_row": (tInvalid), "missing_row": (tMissing), "bad_degree": (tBadDeg), "bad_dict": (tBadDict), "torn": (tTorn), "version_back": (tVerBack), "example": (tEx)}.%ToJSON()
	Quit $$$OK
}

/// The builder job: Graph.KG.Traversal.BuildNKG with the window flag the reader counts in.
ClassMethod Builder(pRun As %String) As %Status
{
	Set ^IVGQ(pRun, "builder") = $Job
	Set ^IVGQ(pRun, "building") = 1
	Set t0 = $ZHorolog
	Try {
		Set tSC = ##class(Graph.KG.Traversal).BuildNKG()
		Set tErr = $Select($$$ISERR(tSC): $System.Status.GetErrorText(tSC), 1: "")
	} Catch ex {
		Set tErr = ex.DisplayString()
	}
	Set ^IVGQ(pRun, "build_seconds") = $ZHorolog - t0
	Set ^IVGQ(pRun, "build_error") = tErr
	Set ^IVGQ(pRun, "building") = 0
	Set ^IVGQ(pRun, "built") = 1
	Quit $$$OK
}

/// The writer job: until ^IVGQ(pRun, "stop_writer"), add edges among the fixture nodes and
/// to new names, and remove edges it added or that the fixture holds, all through
/// Graph.KG.Adjacency. Counts in ^IVGQ(pRun, "w").
ClassMethod Writer(pRun As %String, pPrefix As %String, pNodes As %Integer) As %Status
{
	Set ^IVGQ(pRun, "writer") = $Job
	Set (tAdds, tRems) = 0, tR = 4242
	While '$Get(^IVGQ(pRun, "stop_writer")) {
		Set tR = (tR * 1103515245 + 12345) # 2147483648
		Set tS = pPrefix_((tR # pNodes) + 1)
		If tR # 3 {
			Set tO = $Select(tR # 5 = 0: pPrefix_"w"_(tAdds + 1), 1: pPrefix_(((tR \ 7) # pNodes) + 1))
			$$$ThrowOnError(##class(Graph.KG.Adjacency).Put(0, tS, "PW", tO, 0.5))
			Set tAdds = tAdds + 1
		} Else {
			Set tP = $Order(^KG("out", 0, tS, ""))
			If tP '= "" {
				Set tO = $Order(^KG("out", 0, tS, tP, ""))
				If tO '= "" {
					$$$ThrowOnError(##class(Graph.KG.Adjacency).Remove(0, tS, tP, tO))
					Set tRems = tRems + 1
				}
			}
		}
		Hang 0.0005
	}
	Set ^IVGQ(pRun, "w") = tAdds_" adds, "_tRems_" removes"
	Quit $$$OK
}

/// The mutator job: wait until the rewrite's mark shows phase pPhase with progress >= pMin,
/// then, through Graph.KG.Adjacency, add and remove default-graph edges of sources BEHIND the
/// rewrite (the first ten in walk order, which are also the lowest indices, so behind the
/// sweep too) and IN FRONT of it (the last ten). Additions use label P7 and new target names;
/// removals take a triple no other graph holds. ^IVGQ(pRun, "mut_at" / "mut_end") hold the
/// phase seen before and after the batch, ^IVGQ(pRun, "mut") the counts.
ClassMethod Mutator(pRun As %String, pPrefix As %String, pPhase As %String, pMin As %Integer) As %Status
{
	Set ^IVGQ(pRun, "mutator") = $Job
	Set t0 = $ZHorolog
	For {
		Set tP = ..Phase()
		Quit:($Piece(tP, ":", 1)=pPhase)&&(+$Piece(tP, ":", 2)>=pMin)
		If ($Get(^IVGQ(pRun, "built"))) || (($ZHorolog - t0) > 120) {
			Set ^IVGQ(pRun, "mut_at") = "missed", ^IVGQ(pRun, "mut_end") = "missed"
			Quit
		}
	}
	Quit:$Get(^IVGQ(pRun, "mut_at"))="missed" $$$OK
	Set ^IVGQ(pRun, "mut_at") = tP
	Kill tBehind, tFront
	Set (tNB, tNF) = 0
	If pPhase = "walk" {
		// the walk goes through ^KG("out", 0) in collation order
		Set tS = pPrefix
		For {
			Set tS = $Order(^KG("out", 0, tS))
			Quit:(tS="")||($Extract(tS, 1, $Length(pPrefix))'=pPrefix)||(tNB>=10)
			Set tNB = tNB + 1, tBehind(tNB) = tS
		}
		Set tS = pPrefix_$Char(255)
		For {
			Set tS = $Order(^KG("out", 0, tS), -1)
			Quit:(tS="")||($Extract(tS, 1, $Length(pPrefix))'=pPrefix)||(tNF>=10)
			Set tNF = tNF + 1, tFront(tNF) = tS
		}
	} Else {
		// the sweep goes through ^NKG(-1) in index order
		For tDir = 1, -1 {
			Set tI = ""
			For {
				Set tI = $Order(^NKG(-1, tI), tDir)
				Quit:tI=""
				Set tS = $Get(^NKG("$ND", tI))
				Continue:(tS="")||($Extract(tS, 1, $Length(pPrefix))'=pPrefix)||('$Data(^KG("out", 0, tS)))
				If tDir = 1 { Set tNB = tNB + 1, tBehind(tNB) = tS } Else { Set tNF = tNF + 1, tFront(tNF) = tS }
				Quit:$Select(tDir = 1: tNB, 1: tNF)>=10
			}
		}
	}
	Set (tAdds, tRems) = 0
	For tSide = "b", "f" {
		For tI = 1:1:10 {
			Set tS = $Select(tSide = "b": $Get(tBehind(tI)), 1: $Get(tFront(tI)))
			Continue:tS=""
			$$$ThrowOnError(##class(Graph.KG.Adjacency).Put(0, tS, "P7", pPrefix_"m"_tSide_tI_pPhase, 0.25))
			$$$ThrowOnError(##class(Graph.KG.Adjacency).Put(0, tS, "P7", $Select(tSide = "b": $Get(tFront(tI)), 1: $Get(tBehind(tI))), 0.5))
			Set tAdds = tAdds + 2
			Set tP = $Order(^KG("out", 0, tS, ""))
			While tP '= "" {
				Set tO = $Order(^KG("out", 0, tS, tP, ""))
				While tO '= "" {
					If (tP '= "P7") && '..HeldOutside(tS, tP, tO) {
						$$$ThrowOnError(##class(Graph.KG.Adjacency).Remove(0, tS, tP, tO))
						Set tRems = tRems + 1
						Set tO = "", tP = ""
						Quit
					}
					Set tO = $Order(^KG("out", 0, tS, tP, tO))
				}
				Quit:tP=""
				Set tP = $Order(^KG("out", 0, tS, tP))
			}
		}
	}
	Set ^IVGQ(pRun, "mut_end") = ..Phase()
	Set ^IVGQ(pRun, "mut") = tAdds_" adds, "_tRems_" removes"
	Quit $$$OK
}

ClassMethod HeldOutside(pS, pP, pO) As %Boolean [ Private ]
{
	Set g = 0
	For {
		Set g = $Order(^KG("out", g))
		Quit:g=""
		Return:$Data(^KG("out", g, pS, pP, pO)) 1
	}
	Quit 0
}

ClassMethod StartMutator(pRun As %String, pPrefix As %String, pPhase As %String, pMin As %Integer) As %Integer
{
	Job ##class(IVGTest.NkgEquiv).Mutator(pRun, pPrefix, pPhase, pMin)
	Quit $ZChild
}

ClassMethod StartReader(pRun As %String) As %Integer
{
	Job ##class(IVGTest.NkgEquiv).Reader(pRun)
	Quit $ZChild
}

ClassMethod StartBuilder(pRun As %String) As %Integer
{
	Job ##class(IVGTest.NkgEquiv).Builder(pRun)
	Quit $ZChild
}

ClassMethod StartWriter(pRun As %String, pPrefix As %String, pNodes As %Integer) As %Integer
{
	Job ##class(IVGTest.NkgEquiv).Writer(pRun, pPrefix, pNodes)
	Quit $ZChild
}

// ---- index gaps (D-NKG-SWAP option D) -----------------------------------------------

/// Hide this namespace's Arno configuration (^ArnoAccel) so the rewrite takes its no-Arno
/// branch (dictionary entries swept). Saved under ^IVGQ("arno_save"); ArnoRestore puts it back.
ClassMethod ArnoHide() As %Integer
{
	If '$Data(^IVGQ("arno_save")) {
		Set ^IVGQ("arno_save") = $Data(^ArnoAccel)
		Merge ^IVGQ("arno_save", "g") = ^ArnoAccel
	}
	Kill ^ArnoAccel
	Quit 1
}

ClassMethod ArnoRestore() As %Integer
{
	Quit:'$Data(^IVGQ("arno_save")) 0
	Kill ^ArnoAccel
	Merge ^ArnoAccel = ^IVGQ("arno_save", "g")
	Kill ^IVGQ("arno_save")
	Quit 1
}

/// Configure an Arno library path for the rewrite's Arno branch (the real 1.1.0 library when
/// the file exists; either way it declares no gap tolerance).
ClassMethod ArnoShow(pPath As %String = "/tmp/libarno_callout.so") As %Integer
{
	Kill ^ArnoAccel
	Set ^ArnoAccel("lib_path") = pPath
	Quit 1
}

/// A strongly connected graph over pPrefix_"v1".."v"_pN in the default graph: a ring both ways
/// (P1, P3) and chords both ways (P2), through Graph.KG.Adjacency. Returns the edge count.
ClassMethod GapGen(pPrefix As %String, pN As %Integer = 40) As %Integer
{
	Set tE = 0
	For tI = 1:1:pN {
		Set tA = pPrefix_"v"_tI, tB = pPrefix_"v"_((tI # pN) + 1), tC = pPrefix_"v"_(((tI + 6) # pN) + 1)
		$$$ThrowOnError(##class(Graph.KG.Adjacency).Put(0, tA, "P1", tB, 1))
		$$$ThrowOnError(##class(Graph.KG.Adjacency).Put(0, tB, "P3", tA, 1))
		$$$ThrowOnError(##class(Graph.KG.Adjacency).Put(0, tA, "P2", tC, 2))
		$$$ThrowOnError(##class(Graph.KG.Adjacency).Put(0, tC, "P2", tA, 2))
		Set tE = tE + 4
	}
	Quit tE
}

/// The fixture name with the middle index among pPrefix's interned names.
ClassMethod MiddleName(pPrefix As %String) As %String
{
	Kill ^||ix
	Set tName = pPrefix, tN = 0
	For {
		Set tName = $Order(^NKG("$NI", tName), 1, tI)
		Quit:(tName="")||($Extract(tName, 1, $Length(pPrefix))'=pPrefix)
		Set ^||ix(tI) = tName, tN = tN + 1
	}
	Set tI = "", tK = 0, tOut = ""
	For {
		Set tI = $Order(^||ix(tI), 1, tName)
		Quit:tI=""
		Set tK = tK + 1
		If tK = (tN \ 2) { Set tOut = tName  Quit }
	}
	Kill ^||ix
	Quit tOut
}

/// "nodeCount:gap,gap,..." for indices below nodeCount with no $ND entry.
ClassMethod Gaps() As %String
{
	Set tN = +$Get(^NKG("$meta", "nodeCount")), tGaps = ""
	For tI = 0:1:tN - 1 {
		Set:'$Data(^NKG("$ND", tI)) tGaps = tGaps_$Select(tGaps = "": "", 1: ",")_tI
	}
	Quit tN_":"_tGaps
}

/// The dictionary readers that walk index ranges, as JSON: closeness (MSBFS, classical and
/// harmonic), eigenvector, and the NODEMAP the adjacency export hands the callout (entries,
/// entries with no name, entries whose name is not $ND of that index).
ClassMethod GapReaders() As %String
{
	Set tOut = {}
	Set tOut.classical = ##class(Graph.KG.NKGAccel).ClosenessGlobalMSBFS("classical", "out", 0, 100000)
	Set tOut.harmonic = ##class(Graph.KG.NKGAccel).ClosenessGlobalMSBFS("harmonic", "out", 0, 100000)
	Set tOut.eigenvector = ##class(Graph.KG.NKGAccel).EigenvectorGlobal(100, 0.0000001, 100000)
	Set tX = ##class(Graph.KG.NKGAccelAdjacency).ExportAdjacency()
	Set tMap = $Get(^||adj(1)), (tEntries, tEmpty, tWrong) = 0
	For tL = 2:1:$Length(tMap, $Char(10)) {
		Set tLine = $Piece(tMap, $Char(10), tL)
		Continue:(tLine="")||(tLine="---")
		Set tEntries = tEntries + 1, tI = $Piece(tLine, "="), tName = $Piece(tLine, "=", 2, *)
		Set:tName="" tEmpty = tEmpty + 1
		Set:(tName'="")&&($Get(^NKG("$ND", tI))'=tName) tWrong = tWrong + 1
	}
	Set tOut.nodemap = {"header": ($Piece(tMap, $Char(10), 1)), "entries": (tEntries), "empty": (tEmpty), "wrong": (tWrong), "export": (tX)}
	Kill ^||adj
	Quit tOut.%ToJSON()
}

/// Names (pCat "nodes" or "labels") in snapshot pA and not in pB, and how many of them still
/// have rows in ^NKG (a kept dead entry has none). Returns "only_a with_rows example".
ClassMethod Dead(pA As %String, pB As %String, pCat As %String) As %String
{
	Set (tN, tLive) = 0, tEx = ""
	Set tK = ""
	For {
		Set tK = $Order(^IVGQ(pA, pCat, tK), 1, tI)
		Quit:tK=""
		Continue:$Data(^IVGQ(pB, pCat, tK))
		Set tN = tN + 1
		Set:tEx="" tEx = ..Show(tK)
		If pCat = "nodes" {
			Set:$Data(^NKG(-1, tI))||$Data(^NKG(-2, tI)) tLive = tLive + 1
		} Else {
			Set tS = ""
			For {
				Set tS = $Order(^NKG(-1, tS))
				Quit:tS=""
				If $Data(^NKG(-1, tS, -(tI + 1))) { Set tLive = tLive + 1  Quit }
			}
		}
	}
	Quit tN_" "_tLive_" "_tEx
}

/// The rebuild marker's phase and progress ("walk:2000"), "" when no rewrite is marked.
ClassMethod Phase() As %String
{
	Set tM = $Get(^NKG("$meta", "rebuild"))
	Quit:tM="" ""
	Quit $ListGet(tM, 3)_":"_$ListGet(tM, 4)
}

/// Kill a job outright (the builder, mid-walk or mid-sweep).
ClassMethod KillJob(pPid As %Integer) As %Status
{
	Quit $SYSTEM.Process.Terminate(pPid)
}

/// 1 while process pPid exists.
ClassMethod Alive(pPid As %Integer) As %Boolean
{
	Quit $Data(^$JOB(pPid)) > 0
}

/// Test-only perturbations of ^NKG for the oracle's self-test: change one -1 weight and
/// remove one sketch. Returns the changed row as text.
ClassMethod Perturb() As %String
{
	Set tS = $Order(^NKG(-1, ""))
	Set tP = $Order(^NKG(-1, tS, ""))
	Set tO = $Order(^NKG(-1, tS, tP, ""), 1, tW)
	Set ^NKG(-1, tS, tP, tO) = tW + 1
	Set tS2 = $Order(^NKG("$agg", ""))
	Set tP2 = $Order(^NKG("$agg", tS2, ""))
	Kill ^NKG("$agg", tS2, tP2)
	Quit tS_","_tP_","_tO_" agg "_tS2_","_tP2
}

}
