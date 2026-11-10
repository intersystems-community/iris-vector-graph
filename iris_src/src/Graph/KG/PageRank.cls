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
/// @param pTol       Stop once the scores' summed change in an iteration is below N * pTol, N the
///                   nodes holding a score: networkx's rule, whose N is every node of the graph.
/// @returns JSON array: [{"id":"node","score":0.12}, ...] sorted desc by score
///
/// The scores are networkx's pagerank(G, alpha, personalization=seeds, tol=pTol) on the graph as a
/// multigraph (see Run). Both bidir and revWeight were declared and ignored until 4.0.0. Callers asking for
/// reverse traversal got forward-only scores back with no error, so a seed with no
/// outgoing edges scored itself and nothing else. The semantics here match the Python
/// fallback in _engine/algorithms.py: a node's divisor counts its forward AND reverse
/// edges, and revWeight scales the contribution carried over a reverse hop, never the
/// divisor.
ClassMethod RunJson(seedJson As %String, alpha As %Double = 0.85, maxIter As %Integer = 20, bidir As %Integer = 0, revWeight As %Double = 1.0, pGraph As %String = "", pExclude As %String = "", pLimit As %Integer = 1000, pTol As %Double = 1E-6) As %String
{
	Set pLimit = +pLimit
	Return:'..Run(seedJson, alpha, maxIter, bidir, revWeight, pGraph, pExclude, .ranks, pTol) "[]"

	// Sort by score descending using a score-keyed temp array
	// ^||PPR.Sort(score, node) — process-private, auto-cleaned
	Kill ^||PPR
	Set node = ""
	For {
		Set node = $Order(ranks(node), 1, score)
		Quit:node=""
		// Use negative score for descending $Order
		Set ^||PPR(-$Normalize(score, 12), node) = score
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
			Set n = $Order(^||PPR(negScore, n), 1, score)
			Quit:n=""
			Set obj = ##class(%DynamicObject).%New()
			Do obj.%Set("id", n, "string")
			Do obj.%Set("score", score, "number")
			Do result.%Push(obj)
			Set count = count + 1
			Quit:(pLimit>0)&&(count>=pLimit)
		}
		Quit:(pLimit>0)&&(count>=pLimit)
	}

	Kill ^||PPR
	Return result.%ToJSON()
}

/// Throws unless alpha is a number in the open interval (0, 1) and maxIter a whole number of at least 1 (4.7.0
/// row 8: alpha 2.0 walked, and answered scores of 22.0 and -21.0). Run, so RunJson and every caller of either,
/// and ArnoAccel.PPRNativeJson check with it; the Python methods and CALL ivg.ppr check first, by the same rule.
ClassMethod CheckArgs(alpha As %String, maxIter As %String)
{
	If ('$IsValidNum(alpha)) || ((+alpha) '> 0) || ((+alpha) '< 1) {
		Throw ##class(%Exception.General).%New("IVG", 5001, , "personalized PageRank: alpha must be in the open interval (0, 1), got "_alpha)
	}
	If ('$IsValidNum(maxIter)) || ((+maxIter) '= ((+maxIter) \ 1)) || ((+maxIter) < 1) {
		Throw ##class(%Exception.General).%New("IVG", 5001, , "personalized PageRank: maxIter must be a whole number of at least 1, got "_maxIter)
	}
}

/// pJson, an answer of RunJson, with a row of score 0 for each node of graph pGraph it lacks, after the scored rows
/// and in collation order, to pLimit rows in all (0: every node). networkx's pagerank(G, personalization=...) answers
/// every node of G, 0.0 for one the walk never reaches (4.7.0 row 9: such a node, one with no edge too, had no row).
/// pSchema is the IVG schema holding the nodes table. An answer with no row (no seeds) or of pLimit rows already is
/// returned as it is, without reading the table. The kg_PPR SQL function behind CALL ivg.ppr calls it; the Python
/// methods add the same rows themselves (AlgorithmsMixin._ppr_every_node).
ClassMethod WithUnscored(pJson As %String, pSchema As %String = "Graph_KG", pGraph As %String = "", pLimit As %Integer = 1000) As %String
{
	Set tIn = ##class(%DynamicArray).%FromJSON(pJson)
	Set pLimit = +pLimit
	Return:(tIn.%Size()=0)||((pLimit>0)&&(tIn.%Size()>=pLimit)) pJson
	Set tIter = tIn.%GetIterator()
	While tIter.%GetNext(.tI, .tRow) {
		Set tHave(tRow.id) = ""
	}
	If pGraph = $Char(0) Set pGraph = ""
	// an empty host variable binds as NULL, hence the COALESCE on both sides
	Set tSQL = "SELECT node_id FROM "_pSchema_".nodes WHERE COALESCE(graph_id, '') = COALESCE(?, '')"
	Set tRS = ##class(%SQL.Statement).%ExecDirect(, tSQL, pGraph)
	If tRS.%SQLCODE < 0 {
		Throw ##class(%Exception.SQL).CreateFromSQLCODE(tRS.%SQLCODE, tRS.%Message)
	}
	While tRS.%Next() {
		Set tId = tRS.%Get("node_id")
		Set:'$Data(tHave(tId)) tZero(tId) = ""
	}
	If tRS.%SQLCODE < 0 {
		Throw ##class(%Exception.SQL).CreateFromSQLCODE(tRS.%SQLCODE, tRS.%Message)
	}
	Set tId = ""
	For {
		Set tId = $Order(tZero(tId))
		Quit:tId=""
		Quit:(pLimit>0)&&(tIn.%Size()>=pLimit)
		Set tRow = ##class(%DynamicObject).%New()
		Do tRow.%Set("id", tId, "string")
		Do tRow.%Set("score", 0, "number")
		Do tIn.%Push(tRow)
	}
	Return tIn.%ToJSON()
}

/// RunJson's walk, leaving ranks(node) = score for every scored node rather than a JSON
/// string, so a caller in IRIS can use every score without the string size limit
/// (spec 235, GroupByPatient). Returns 0, with ranks empty, when there are no seeds.
///
/// It is networkx's pagerank(G, alpha, personalization=seeds, tol=pTol), G the graph's adjacency as a
/// multigraph: an edge under each predicate counts, and a keyed triple counts its "#" edges (spec 234), in
/// the divisor and in what the walk passes along it alike. A seed named twice is one seed. A node with no
/// out-edges (none kept, under pExclude; with bidir, none either way) hands alpha * its score to the seeds,
/// evenly, as the restart does, so every iteration's scores sum to 1 (with bidir, when revWeight is 1).
/// Before 4.6.0 (R270) such a node kept (1 - alpha) of its score and lost the rest, so the scores summed to
/// less than 1, and the walk stopped once no score moved by 1E-4 whatever the caller's tolerance; pTol is
/// read now, and the stop rule is networkx's (RunJson's pTol).
ClassMethod Run(seedJson As %String, alpha As %Double, maxIter As %Integer, bidir As %Integer, revWeight As %Double, pGraph As %String, pExclude As %String, Output ranks, pTol As %Double = 1E-6) As %Boolean
{
	Kill ranks
	Do ..CheckArgs(alpha, maxIter)
	// Until 4.0.1 pGraph was hardwired to 0: every PPR walked the default graph
	// whatever graph its seeds lived in (spec 231, FR-016).
	Set pGraph = ##class(Graph.KG.GraphKey).ForIndex(pGraph)
	If seedJson = "" Return 0
	Set hasExcl = ..ParseExclude(pExclude, .excl)
	Set revWeight = +revWeight
	Set wantRev = ((+bidir) '= 0) && (revWeight > 0)
	Set pTol = $Select(pTol="": 1E-6, 1: +pTol)

	Try {
		Set seedArr = ##class(%DynamicArray).%FromJSON(seedJson)
	} Catch seedEx {
		Do ##class(%SYS.System).WriteToConsoleLog("Graph.KG.PageRank: seed JSON parse failed: "_seedEx.DisplayString(), 0, 2)
		Return 0
	}
	If seedArr.%Size() = 0 Return 0

	// Build the seed set (a seed named twice is one seed) and the initial ranks
	For i = 0:1:(seedArr.%Size() - 1) {
		Set seeds(seedArr.%Get(i)) = ""
	}
	Set nSeeds = 0
	Set s = ""
	For {
		Set s = $Order(seeds(s))
		Quit:s=""
		Set nSeeds = nSeeds + 1
	}
	Set teleport = 1.0 / nSeeds
	Set s = ""
	For {
		Set s = $Order(seeds(s))
		Quit:s=""
		Set ranks(s) = teleport
	}

	// Iterative power method
	For iter = 1:1:maxIter {
		Kill newRanks
		// the score of the nodes with no out-edges this iteration, which goes to the seeds
		Set dangling = 0

		Set node = ""
		For {
			Set node = $Order(ranks(node), 1, rank)
			Quit:node=""

			// The out-degree is counted once per node and memoised for the rest of the
			// walk, so the adjacency is read O(E) once rather than once per iteration.
			// Graph.KG.Adj.Degree counts a keyed triple's "#" edges.
			If '$Data(outDeg(node)) {
				Set outDeg(node) = ##class(Graph.KG.Adj).Degree(pGraph, node)
			}
			Set deg = outDeg(node)
			If hasExcl {
				// Count the kept out-edges rather than subtract from a stored count, so the
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
							Set fDeg = fDeg + ..Multiplicity(pGraph, node, p, o)
						}
					}
					Set fwdDeg(node) = fDeg
				}
				Set deg = fwdDeg(node)
			}
			Set degTot = deg
			If wantRev {
				// No stored counter holds in-degree, so count the reverse edges. The adjacency cannot change underneath us
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
							Set rDeg = rDeg + ..Multiplicity(pGraph, rs, rp, node)
						}
					}
					Set revDeg(node) = rDeg
					Set degTot = deg + rDeg
				}
			}
			If degTot = 0 {
				Set dangling = dangling + rank
				Continue
			}

			Set share = (alpha * rank) / degTot
			Set p = ""
			For {
				Set p = $Order(^KG("out", pGraph, node, p))
				Quit:p=""
				Continue:hasExcl&&$Data(excl(p))&&..Excluded(.excl, node, p)
				Set o = ""
				For {
					Set o = $Order(^KG("out", pGraph, node, p, o))
					Quit:o=""
					Set newRanks(o) = $Get(newRanks(o), 0) + (share * ..Multiplicity(pGraph, node, p, o))
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
						Set newRanks(rs) = $Get(newRanks(rs), 0) + (revShare * ..Multiplicity(pGraph, rs, rp, node))
					}
				}
			}
		}

		// the restart and the dangling nodes' score, both to the seeds evenly (networkx's
		// dangling=None: the personalization's proportions)
		Set restart = ((1 - alpha) + (alpha * dangling)) * teleport
		Set s = ""
		For {
			Set s = $Order(seeds(s))
			Quit:s=""
			Set newRanks(s) = $Get(newRanks(s), 0) + restart
		}

		// networkx's stop rule: the summed change below N * pTol, N the nodes holding a score
		Set change = 0
		Set held = 0
		Set ck = ""
		For {
			Set ck = $Order(newRanks(ck), 1, newVal)
			Quit:ck=""
			Set change = change + $ZAbs(newVal - $Get(ranks(ck), 0))
			Set:newVal'=0 held = held + 1
		}
		Set ck = ""
		For {
			Set ck = $Order(ranks(ck), 1, oldVal)
			Quit:ck=""
			Set:'$Data(newRanks(ck)) change = change + $ZAbs(oldVal)
		}
		Kill ranks
		Merge ranks = newRanks
		Quit:change<(held*pTol)
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

/// Global PageRank over the default graph's adjacency ^KG("out", 0, s, p, o), as textbook PageRank:
///   - the nodes are every source and every target (N of them);
///   - a node passes damping * rank along its out-edges in proportion to their multiplicity: a keyed triple counts
///     its "#" edges, as Graph.KG.Adj.Count does;
///   - a node with no out-edges spreads damping * rank over all N nodes;
///   - every node gets (1 - damping) / N, so the scores sum to 1.
/// It runs maxIter iterations and stops early only at a fixed point (no score moves by 1E-15).
/// Before 4.5.2 a node with no out-edges kept (1 - damping) * rank and passed the rest nowhere, N counted only the
/// nodes with out-edges, and a parallel edge counted in the degree but passed rank once: the scores summed to about
/// 0.57 on an 8-node path (tests/e2e/test_pagerank_textbook_e2e.py).
/// pEdgeless: the graph's nodes with no edge, a JSON array of ids (4.6.0 R306). The adjacency holds none of them;
/// each takes part as a node with no out-edge, so its rank is spread over every node and the scores sum to 1 over
/// node_count() nodes, networkx's pagerank(G). Before R306 they were left out.
ClassMethod PageRankGlobalJson(damping As %Double = 0.85, maxIter As %Integer = 20, pEdgeless As %String = "") As %String
{
	Set pGraph = 0
	// One pass over the adjacency, before the iteration loop: the node set and each source's out-weight, O(E) once.
	Set node = ""
	For {
		Set node = $Order(^KG("out", pGraph, node))
		Quit:node=""
		Set ranks(node) = 0, w = 0, p = ""
		For {
			Set p = $Order(^KG("out", pGraph, node, p))
			Quit:p=""
			Set o = ""
			For {
				Set o = $Order(^KG("out", pGraph, node, p, o))
				Quit:o=""
				Set ranks(o) = 0
				Set w = w + ..Multiplicity(pGraph, node, p, o)
			}
		}
		Set:w>0 outW(node) = w
	}
	If pEdgeless '= "" {
		Set tIter = ##class(%DynamicArray).%FromJSON(pEdgeless).%GetIterator()
		While tIter.%GetNext(.tKey, .tId) {
			Set ranks(tId) = 0
		}
	}
	Set nodeCount = 0, node = ""
	For {
		Set node = $Order(ranks(node))
		Quit:node=""
		Set nodeCount = nodeCount + 1
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
		Set dangling = 0
		Set node = ""
		For {
			Set node = $Order(ranks(node), 1, rank)
			Quit:node=""
			If '$Data(outW(node)) {
				Set dangling = dangling + rank
				Continue
			}
			Set share = (damping * rank) / outW(node)
			Set p = ""
			For {
				Set p = $Order(^KG("out", pGraph, node, p))
				Quit:p=""
				Set o = ""
				For {
					Set o = $Order(^KG("out", pGraph, node, p, o))
					Quit:o=""
					Set newRanks(o) = $Get(newRanks(o), 0) + (share * ..Multiplicity(pGraph, node, p, o))
				}
			}
		}
		
		// the teleport and the rank of the nodes with no out-edges, spread evenly
		Set base = ((1 - damping) + (damping * dangling)) / nodeCount
		Set maxDelta = 0
		Set node = ""
		For {
			Set node = $Order(ranks(node), 1, rank)
			Quit:node=""
			Set newVal = $Get(newRanks(node), 0) + base
			Set newRanks(node) = newVal
			Set delta = $ZAbs(newVal - rank)
			Set:delta>maxDelta maxDelta = delta
		}
		Kill ranks
		Merge ranks = newRanks
		Quit:maxDelta<1E-15
	}
	
	Kill ^||PPR
	Set node = ""
	For {
		Set node = $Order(ranks(node), 1, score)
		Quit:node=""
		Set ^||PPR(-$Normalize(score, 12), node) = score
	}
	
	Set result = ##class(%DynamicArray).%New()
	Set negScore = ""
	Set count = 0
	For {
		Set negScore = $Order(^||PPR(negScore))
		Quit:negScore=""
		Set n = ""
		For {
			Set n = $Order(^||PPR(negScore, n), 1, score)
			Quit:n=""
			Set obj = ##class(%DynamicObject).%New()
			Do obj.%Set("id", n, "string")
			Do obj.%Set("score", score, "number")
			Do result.%Push(obj)
			Set count = count + 1
			Quit:count>=10000
		}
		Quit:count>=10000
	}
	Kill ^||PPR
	Return result.%ToJSON()
}

/// The number of edges the triple (pSrc, pPred, pDst) carries: its "#" count when it is keyed (spec 234), else 1.
ClassMethod Multiplicity(pGraph, pSrc As %String, pPred As %String, pDst As %String) As %Integer [ Internal ]
{
	Quit:$Data(^KG("out", pGraph, pSrc, pPred, pDst, "#")) +^KG("out", pGraph, pSrc, pPred, pDst, "#")
	Quit 1
}

}
