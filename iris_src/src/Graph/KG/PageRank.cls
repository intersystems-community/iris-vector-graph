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
/// @returns JSON array: [{"id":"node","score":0.12}, ...] sorted desc by score
///
/// Both bidir and revWeight were declared and ignored until 4.0.0. Callers asking for
/// reverse traversal got forward-only scores back with no error, so a seed with no
/// outgoing edges scored itself and nothing else. The semantics here match the Python
/// fallback in _engine/algorithms.py: a node's divisor counts its forward AND reverse
/// edges, and revWeight scales the contribution carried over a reverse hop, never the
/// divisor.
ClassMethod RunJson(seedJson As %String, alpha As %Double = 0.85, maxIter As %Integer = 20, bidir As %Integer = 0, revWeight As %Double = 1.0, pGraph As %String = "") As %String
{
	// Until 4.0.1 pGraph was hardwired to 0: every PPR walked the default graph
	// whatever graph its seeds lived in (spec 231, FR-016).
	Set pGraph = ##class(Graph.KG.GraphKey).ForIndex(pGraph)
	If seedJson = "" Return "[]"
	Set revWeight = +revWeight
	Set wantRev = ((+bidir) '= 0) && (revWeight > 0)
	
	Try {
		Set seedArr = ##class(%DynamicArray).%FromJSON(seedJson)
	} Catch seedEx {
		Do ##class(%SYS.System).WriteToConsoleLog("Graph.KG.PageRank: seed JSON parse failed: "_seedEx.DisplayString(), 0, 2)
		Return "[]"
	}
	If seedArr.%Size() = 0 Return "[]"
	
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
						Set rs = ""
						For {
							Set rs = $Order(^KG("in", pGraph, node, rp, rs))
							Quit:rs=""
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
					Set rs = ""
					For {
						Set rs = $Order(^KG("in", pGraph, node, rp, rs))
						Quit:rs=""
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
	
	// Build JSON result array (top 1000)
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
			Quit:count>=1000
		}
		Quit:count>=1000
	}
	
	Kill ^||PPR
	Return result.%ToJSON()
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
