/// High-performance Personalized PageRank (PPR) for the Knowledge Graph
/// Pure ObjectScript — no iris module dependency, safe from any call context.
Class Graph.KG.PageRank Extends %RegisteredObject [ SqlSchemaName = Graph_KG ]
{

/// Run PPR and return results as a JSON string.
/// Pure ObjectScript: walks ^KG globals natively with $ORDER/$GET.
/// Works from SQL stored procedure, native API bridge, or embedded Python.
///
/// @param seedJson   JSON array of seed node IDs, e.g. '["A","B"]'
/// @param alpha      Damping factor (default 0.85)
/// @param maxIter    Max iterations (default 20)
/// @param bidir      1 = also traverse edges backwards via ^KG("in")
/// @param revWeight  Multiplier on a reverse hop's contribution (bidir only)
/// @param pGraph     Graph name; "" is the default graph. Derived through GraphKey.ForIndex.
/// @param pExclude   JSON array of edges the walk skips (spec 235): "p" is predicate p from
///                   any source, "Type.p" is p only where the edge's source key starts
///                   "Type/". "" or "[]" skips nothing; a malformed list throws. An excluded
///                   edge is gone from both the walk and its source's divisor, so the scores
///                   equal a walk of the graph without it.
/// @param pLimit     How many nodes to return; 0 is every scored node.
/// @returns JSON array: [{"id":"node","score":0.12}, ...] sorted desc by score
///
/// Both bidir and revWeight were declared and ignored until 4.0.0. Callers asking for
/// reverse traversal got forward-only scores back with no error, so a seed with no
/// outgoing edges scored itself and nothing else. The semantics here match the Python
/// fallback in _engine/algorithms.py: a node's divisor counts its forward AND reverse
/// edges, and revWeight scales the contribution carried over a reverse hop, never the
/// divisor.
ClassMethod RunJson(seedJson As %String, alpha As %Double = 0.85, maxIter As %Integer = 20, bidir As %Integer = 0, revWeight As %Double = 1.0, pGraph As %String = "", pExclude As %String = "", pLimit As %Integer = 1000) As %String
{
	Set pLimit = +pLimit
	Return:'..Run(seedJson, alpha, maxIter, bidir, revWeight, pGraph, pExclude, .ranks) "[]"
	
	// Sort by score descending using a score-keyed temp array
	// ^||PPR.Sort(score, node) — process-private, auto-cleaned
	Kill ^||PPR
	Set node = ""
	For {
		Set node = $Order(ranks(node), 1, score)
		Quit:node=""
		// Use negative score for descending $Order
		Set ^||PPR(-score, node) = ""
	}
	
	// Build JSON result array (top pLimit; 0 is every node)
	Set result = ##class(%DynamicArray).%New()
	Set negScore = ""
	Set count = 0
	For {
		Set negScore = $Order(^||PPR(negScore))
		Quit:negScore=""
		Set n = ""
		For {
			Set n = $Order(^||PPR(negScore, n))
			Quit:n=""
			Set obj = ##class(%DynamicObject).%New()
			Do obj.%Set("id", n)
			Do obj.%Set("score", -negScore, "number")
			Do result.%Push(obj)
			Set count = count + 1
			Quit:(pLimit>0)&&(count>=pLimit)
		}
		Quit:(pLimit>0)&&(count>=pLimit)
	}
	
	Kill ^||PPR
	Return result.%ToJSON()
}

/// RunJson's walk, leaving ranks(node) = score for every scored node rather than a JSON
/// string, so a caller in IRIS can use every score without the string size limit
/// (spec 235, GroupByPatient). Returns 0, with ranks empty, when there are no seeds.
ClassMethod Run(seedJson As %String, alpha As %Double, maxIter As %Integer, bidir As %Integer, revWeight As %Double, pGraph As %String, pExclude As %String, Output ranks) As %Boolean
{
	Kill ranks
	// Until 4.0.1 pGraph was hardwired to 0: every PPR walked the default graph
	// whatever graph its seeds lived in (spec 231, FR-016).
	Set pGraph = ##class(Graph.KG.GraphKey).ForIndex(pGraph)
	If seedJson = "" Return 0
	Set hasExcl = ..ParseExclude(pExclude, .excl)
	Set revWeight = +revWeight
	Set wantRev = ((+bidir) '= 0) && (revWeight > 0)
	
	Try {
		Set seedArr = ##class(%DynamicArray).%FromJSON(seedJson)
	} Catch seedEx {
		Do ##class(%SYS.System).WriteToConsoleLog("Graph.KG.PageRank: seed JSON parse failed: "_seedEx.DisplayString(), 0, 2)
		Return 0
	}
	If seedArr.%Size() = 0 Return 0
	
	// Build seed set and initial ranks
	Set nSeeds = seedArr.%Size()
	Set teleport = 1.0 / nSeeds
	For i = 0:1:(nSeeds - 1) {
		Set s = seedArr.%Get(i)
		Set seeds(s) = ""
		Set ranks(s) = teleport
	}
	
	// Iterative power method
	For iter = 1:1:maxIter {
		Kill newRanks
		
		Set node = ""
		For {
			Set node = $Order(ranks(node), 1, rank)
			Quit:node=""
			
			Set deg = +$Get(^KG("deg", pGraph, node))
			If hasExcl {
				// Count the kept out-edges rather than subtract from ^KG("deg"), so the
				// divisor is the one a graph without the excluded edges would hold.
				If '$Data(fwdDeg(node)) {
					Set fDeg = 0
					Set p = ""
					For {
						Set p = $Order(^KG("out", pGraph, node, p))
						Quit:p=""
						Continue:$Data(excl(p))&&..Excluded(.excl, node, p)
						Set o = ""
						For {
							Set o = $Order(^KG("out", pGraph, node, p, o))
							Quit:o=""
							Set fDeg = fDeg + 1
						}
					}
					Set fwdDeg(node) = fDeg
				}
				Set deg = fwdDeg(node)
			}
			Set degTot = deg
			If wantRev {
				// No ^KG counter holds in-degree — ^KG("degp") is per-predicate *out*-degree
				// — so count the reverse edges. The adjacency cannot change underneath us
				// here, so cache the count and pay for it once rather than once per iteration.
				If $Data(revDeg(node)) {
					Set degTot = deg + revDeg(node)
				} Else {
					Set rDeg = 0
					Set rp = ""
					For {
						Set rp = $Order(^KG("in", pGraph, node, rp))
						Quit:rp=""
						Set rX = $Select('hasExcl: 0, '$Data(excl(rp)): 0, $Data(excl(rp)) # 2: 1, 1: 2)
						Continue:rX=1
						Set rs = ""
						For {
							Set rs = $Order(^KG("in", pGraph, node, rp, rs))
							Quit:rs=""
							Continue:(rX=2)&&..Excluded(.excl, rs, rp)
							Set rDeg = rDeg + 1
						}
					}
					Set revDeg(node) = rDeg
					Set degTot = deg + rDeg
				}
			}
			If degTot = 0 {
				Set newRanks(node) = $Get(newRanks(node), 0) + (rank * (1 - alpha))
				Continue
			}

			Set p = ""
			For {
				Set p = $Order(^KG("out", pGraph, node, p))
				Quit:p=""
				Continue:hasExcl&&$Data(excl(p))&&..Excluded(.excl, node, p)
				Set share = (alpha * rank) / degTot
				Set o = ""
				For {
					Set o = $Order(^KG("out", pGraph, node, p, o))
					Quit:o=""
					Set newRanks(o) = $Get(newRanks(o), 0) + share
				}
			}

			If wantRev {
				Set revShare = ((alpha * revWeight) * rank) / degTot
				Set rp = ""
				For {
					Set rp = $Order(^KG("in", pGraph, node, rp))
					Quit:rp=""
					Set rX = $Select('hasExcl: 0, '$Data(excl(rp)): 0, $Data(excl(rp)) # 2: 1, 1: 2)
					Continue:rX=1
					Set rs = ""
					For {
						Set rs = $Order(^KG("in", pGraph, node, rp, rs))
						Quit:rs=""
						Continue:(rX=2)&&..Excluded(.excl, rs, rp)
						Set newRanks(rs) = $Get(newRanks(rs), 0) + revShare
					}
				}
			}
		}
		
		Set teleportShare = (1 - alpha) * teleport
		Set s = ""
		For {
			Set s = $Order(seeds(s))
			Quit:s=""
			Set newRanks(s) = $Get(newRanks(s), 0) + teleportShare
		}
		
		// Early termination: check max rank delta between iterations
		If iter > 2 {
			Set maxDelta = 0
			Set ck = ""
			For {
				Set ck = $Order(newRanks(ck), 1, newVal)
				Quit:ck=""
				Set oldVal = +$Get(ranks(ck))
				Set delta = newVal - oldVal
				If delta < 0 Set delta = -delta
				If delta > maxDelta Set maxDelta = delta
			}
			If maxDelta < 0.0001 {
				Kill ranks
				Merge ranks = newRanks
				Quit
			}
		}
		
		Kill ranks
		Merge ranks = newRanks
	}
	Return 1
}

/// Parse RunJson's pExclude into excl(p) for a bare predicate and excl(p, Type) for a
/// typed one. Returns 1 when anything is excluded. Throws on anything but a JSON array
/// of "p" / "Type.p" strings, so a typo is an error rather than an unfiltered walk.
ClassMethod ParseExclude(pExclude As %String, Output excl) As %Boolean [ Internal ]
{
	Kill excl
	If pExclude = "" Return 0
	Try {
		Set arr = ##class(%DynamicArray).%FromJSON(pExclude)
	} Catch parseEx {
		Throw ##class(%Exception.General).%New("PPRExclude", 5001, , "pExclude is not JSON: "_pExclude)
	}
	If '$IsObject(arr) || (arr.%ClassName(1) '= "%Library.DynamicArray") {
		Throw ##class(%Exception.General).%New("PPRExclude", 5001, , "pExclude must be a JSON array")
	}
	Set hasAny = 0
	For i = 0:1:(arr.%Size() - 1) {
		Set entry = arr.%Get(i)
		Set bad = (arr.%GetTypeOf(i) '= "string") || (entry = "") || ($Length(entry, ".") > 2)
		If 'bad && ($Length(entry, ".") = 2) {
			Set bad = ($Piece(entry, ".", 1) = "") || ($Piece(entry, ".", 2) = "")
		}
		If bad {
			Throw ##class(%Exception.General).%New("PPRExclude", 5001, , "malformed pExclude entry: "_entry)
		}
		If $Length(entry, ".") = 2 {
			Set excl($Piece(entry, ".", 2), $Piece(entry, ".", 1)) = ""
		} Else {
			Set excl(entry) = ""
		}
		Set hasAny = 1
	}
	Return hasAny
}

/// 1 when the edge source -pred-> is excluded: a bare entry for pred, or a typed entry
/// for pred whose Type is the source key's prefix before "/".
ClassMethod Excluded(ByRef excl, source As %String, pred As %String) As %Boolean [ Internal ]
{
	If '$Data(excl(pred)) Return 0
	If $Data(excl(pred)) # 2 Return 1
	If source '[ "/" Return 0
	Return $Data(excl(pred, $Piece(source, "/", 1))) > 0
}

ClassMethod PageRankGlobalJson(damping As %Double = 0.85, maxIter As %Integer = 20) As %String
{
	Set pGraph = 0
	Set nodeCount = 0
	Set node = ""
	For {
		Set node = $Order(^KG("deg", pGraph, node))
		Quit:node=""
		Set nodeCount = nodeCount + 1
		Set ranks(node) = 1
	}
	If nodeCount = 0 Return "[]"
	
	Set initRank = 1.0 / nodeCount
	Set node = ""
	For {
		Set node = $Order(ranks(node))
		Quit:node=""
		Set ranks(node) = initRank
	}
	
	For iter = 1:1:maxIter {
		Kill newRanks
		
		Set node = ""
		For {
			Set node = $Order(ranks(node), 1, rank)
			Quit:node=""
			Set deg = +$Get(^KG("deg", pGraph, node))
			If deg = 0 {
				Set newRanks(node) = $Get(newRanks(node), 0) + (rank * (1 - damping))
				Continue
			}
			Set p = ""
			For {
				Set p = $Order(^KG("out", pGraph, node, p))
				Quit:p=""
				Set share = (damping * rank) / deg
				Set o = ""
				For {
					Set o = $Order(^KG("out", pGraph, node, p, o))
					Quit:o=""
					Set newRanks(o) = $Get(newRanks(o), 0) + share
				}
			}
		}
		
		Set teleportShare = (1 - damping) / nodeCount
		Set node = ""
		For {
			Set node = $Order(ranks(node))
			Quit:node=""
			Set newRanks(node) = $Get(newRanks(node), 0) + teleportShare
		}
		
		If iter > 2 {
			Set maxDelta = 0
			Set ck = ""
			For {
				Set ck = $Order(newRanks(ck), 1, newVal)
				Quit:ck=""
				Set delta = newVal - (+$Get(ranks(ck)))
				If delta < 0 Set delta = -delta
				If delta > maxDelta Set maxDelta = delta
			}
			If maxDelta < 0.0001 {
				Kill ranks
				Merge ranks = newRanks
				Quit
			}
		}
		
		Kill ranks
		Merge ranks = newRanks
	}
	
	Kill ^||PPR
	Set node = ""
	For {
		Set node = $Order(ranks(node), 1, score)
		Quit:node=""
		Set ^||PPR(-score, node) = ""
	}
	
	Set result = ##class(%DynamicArray).%New()
	Set negScore = ""
	Set count = 0
	For {
		Set negScore = $Order(^||PPR(negScore))
		Quit:negScore=""
		Set n = ""
		For {
			Set n = $Order(^||PPR(negScore, n))
			Quit:n=""
			Set obj = ##class(%DynamicObject).%New()
			Do obj.%Set("id", n)
			Do obj.%Set("score", -negScore, "number")
			Do result.%Push(obj)
			Set count = count + 1
			Quit:count>=10000
		}
		Quit:count>=10000
	}
	Kill ^||PPR
	Return result.%ToJSON()
}

}
