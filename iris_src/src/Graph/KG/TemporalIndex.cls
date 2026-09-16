Class Graph.KG.TemporalIndex Extends %RegisteredObject
{

Parameter BUCKET = 300;

/// Timestamp precision unit. "" = seconds (default). "ms" = milliseconds.
Parameter TSUNIT As %String = "";

/// Bucket width in milliseconds when TSUNIT="ms". Derived: BUCKET * 1000.
Parameter BUCKETMS As %Integer = 300000;

/// Render a numeric value as valid JSON.
/// ObjectScript renders 0.5 as ".5" and -0.5 as "-.5" — both are not JSON.
/// Empty string is stored as null.
ClassMethod jsonNum(pValue As %String) As %String [ Internal ]
{
	If pValue = "" { Return "null" }
	If $Extract(pValue, 1, 2) = "-." { Return "-0" _ $Extract(pValue, 2, *) }
	If $Extract(pValue, 1) = "." { Return "0" _ pValue }
	Return pValue
}

/// Escape a string for embedding inside a JSON double-quoted value.
/// Handles backslash, double-quote, and ASCII control characters.
ClassMethod jsonEsc(pValue As %String) As %String [ Internal ]
{
	// RFC 8259 §7: U+0000–U+001F must be escaped. Named escapes for the 5
	// common ones; \uXXXX for the rest (NUL, SOH … ETB, EM … US).
	Set result = $Replace(pValue, "\", "\\")
	Set result = $Replace(result, """", "\""")
	Set result = $Replace(result, $Char(8),  "\b")
	Set result = $Replace(result, $Char(9),  "\t")
	Set result = $Replace(result, $Char(10), "\n")
	Set result = $Replace(result, $Char(12), "\f")
	Set result = $Replace(result, $Char(13), "\r")
	// Remaining control chars U+0000–U+001F (NUL and 0x01–0x07, 0x0B, 0x0E–0x1F)
	For i = 0:1:7,11,14:1:31 {
		If result [ $Char(i) {
			Set hex = $Select(i<16: "000"_$ZHex(i), 1: "00"_$ZHex(i))
			Set result = $Replace(result, $Char(i), "\u"_hex)
		}
	}
	Return result
}

ClassMethod InsertEdge(graphId As %String = "", source As %String, predicate As %String, target As %String, timestamp As %Integer = "", weight As %Double = 1.0, attrsJSON As %String = "", upsert As %Boolean = 0, suppressReverseIndex As %Boolean = 0, mode As %String = "") As %Status
{
	// Spec-223: graphId is the first subscript of all temporal globals.
	// Empty string normalises to integer 0 (default graph) — same convention as spec-214.
	Set tGKey = ##class(Graph.KG.GraphKey).ForIndex(graphId)
	If timestamp = "" Set timestamp = ##class(%SYSTEM.SQL).DATEDIFF("ss", "1970-01-01 00:00:00", $ZDateTime($ZTimeStamp, 3, 1))
	// Coerce empty weight to 1 — same guard as the batch path.
	If weight = "" Set weight = 1
	// Resolve effective mode: explicit mode overrides legacy upsert boolean.
	If mode = "" { Set mode = $Select(upsert: "update", 1: "insert") }
	Set tAttrs = ""
	If attrsJSON '= "" { Set tAttrs = ##class(%DynamicObject).%FromJSON(attrsJSON) }
	// The stores are written in one place. Spec-221 fixed the upsert semantics
	// here while BulkInsert kept a copy of this body and kept the old meaning;
	// two copies of the write is what let one path be fixed and the other not.
	Do ..WriteStores(tGKey, timestamp, source, predicate, target, weight, suppressReverseIndex, mode, tAttrs)
	// 1 whether or not the edge was written: a skip is not an error.
	Return 1
}

/// Write one temporal edge into every store the index maintains.
/// Returns 1 when the edge was written, 0 when <var>pMode</var> is "skip" and an
/// edge already sits at those coordinates. <var>pAttrs</var> is a %DynamicObject
/// or "".
/// Known limitation (spec-221 FR-004/FR-011): "update" overwrites the weight in
/// ^KG("tout") and ^KG("tin") but does not adjust the ^KG("tagg") bucket
/// aggregates or the HLL sketch, which still carry the original weight.
ClassMethod WriteStores(pGKey, pTs As %Integer, pS As %String, pP As %String, pO As %String, pW As %Double, pSuppR As %Boolean = 0, pMode As %String = "insert", pAttrs = "") As %Boolean [ Private ]
{
	If pMode = "skip" && $Data(^KG("tout", pGKey, pTs, pS, pP, pO)) { Return 0 }
	If pMode = "update" {
		Kill ^KG("edgeprop", pGKey, pTs, pS, pP, pO)
	}
	Set ^KG("tout", pGKey, pTs, pS, pP, pO) = pW
	If 'pSuppR { Set ^KG("tin", pGKey, pTs, pO, pP, pS) = pW }
	Set tBucketDiv = $Select(..#TSUNIT="ms": ..#BUCKETMS, 1: ..#BUCKET)
	Set tBucket = pTs \ tBucketDiv
	Set tmp = $Increment(^KG("bucket", pGKey, tBucket, pS))
	// Structural adjacency shadow, in this graph, so BFS and traversal see the
	// edge. Owned by EdgeScan: writing the four stores here incremented
	// ^KG("deg") once per timestamp against a single ^KG("out") entry, so the
	// degree over-counted every triple carrying more than one timestamp, and
	// ^KG("degp") was never written at all.
	Do ##class(Graph.KG.EdgeScan).WriteAdjacencyShadow(pS, pP, pO, pW, pGKey)
	// count and sum are atomic via $Increment
	Set tmp = $Increment(^KG("tagg", pGKey, tBucket, pS, pP, "count"))
	Set tmp = $Increment(^KG("tagg", pGKey, tBucket, pS, pP, "sum"), pW)
	If '$Data(^KG("tagg", pGKey, tBucket, pS, pP, "min")) || (pW < ^KG("tagg", pGKey, tBucket, pS, pP, "min")) {
		Set ^KG("tagg", pGKey, tBucket, pS, pP, "min") = pW
	}
	If '$Data(^KG("tagg", pGKey, tBucket, pS, pP, "max")) || (pW > ^KG("tagg", pGKey, tBucket, pS, pP, "max")) {
		Set ^KG("tagg", pGKey, tBucket, pS, pP, "max") = pW
	}
	Do ..UpdateHLL(pGKey, tBucket, pS, pP, pO)
	If $IsObject(pAttrs) {
		Set iter = pAttrs.%GetIterator()
		While iter.%GetNext(.key, .val) {
			Set ^KG("edgeprop", pGKey, pTs, pS, pP, pO, key) = val
		}
	}
	Return 1
}

/// Write a batch of temporal edges. <var>mode</var> means what it means on
/// InsertEdge: "update" overwrites an edge already at those coordinates, "skip"
/// keeps the first write, "insert" writes unconditionally. Empty defers to the
/// legacy <var>upsert</var> boolean, which is "update".
/// Returns the number of edges written — a skipped edge is not counted.
ClassMethod BulkInsert(graphId As %String = "", batchJSON As %String, upsert As %Boolean = 0, mode As %String = "") As %Integer
{
	Set tGKey = ##class(Graph.KG.GraphKey).ForIndex(graphId)
	Set arr = ##class(%DynamicArray).%FromJSON(batchJSON)
	Set n = arr.%Size()
	Set count = 0
	// Resolved once for the batch, the same way InsertEdge resolves it per edge.
	If mode = "" { Set mode = $Select(upsert: "update", 1: "insert") }
	For i = 0:1:(n-1) {
		Set item = arr.%Get(i)
		Set source = item.%Get("s")
		Set predicate = item.%Get("p")
		Set target = item.%Get("o")
		Set ts = +item.%Get("ts")
		Set weight = item.%Get("w")
		If weight = "" Set weight = 1
		If ts = 0 Set ts = ##class(%SYSTEM.SQL).DATEDIFF("ss", "1970-01-01 00:00:00", $ZDateTime($ZTimeStamp, 3, 1))
		Set attrs = item.%Get("attrs")
		If '$IsObject(attrs) { Set attrs = "" }
		Set count = count + ..WriteStores(tGKey, ts, source, predicate, target, weight, +item.%Get("sri"), mode, attrs)
	}
	Return count
}

ClassMethod UpdateHLL(graphKey, bucket As %Integer, source As %String, predicate As %String, value As %String) [ Private ]
{
	Set hll = $Get(^KG("tagg", graphKey, bucket, source, predicate, "hll"), ..HLLEmpty())
	Set before = hll
	Do ..HLLAdd(.hll, value)
	If hll '= before {
		Set ^KG("tagg", graphKey, bucket, source, predicate, "hll") = hll
	}
}

/// An all-zero 16-register sketch.
ClassMethod HLLEmpty() As %String [ Private ]
{
	Quit $ListBuild(0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0)
}

/// Fold one value into a 16-register HLL sketch, in place.
///
/// SHA1 first byte -> register index, second byte -> leading-zero count.
///
/// Lives apart from UpdateHLL because the sketch is not invertible: a register
/// only ever moves up, so removing one edge means rebuilding the bucket's sketch
/// from the edges that survive. A rebuild that computed registers even slightly
/// differently from the write would report drift that was not there, so both
/// paths share this one implementation.
ClassMethod HLLAdd(ByRef pHLL, pValue As %String) [ Private ]
{
	Set tHashBytes = $SYSTEM.Encryption.SHA1Hash(pValue)
	Set tRegIdx = ($ASCII(tHashBytes, 1) # 16) + 1
	Set tB1 = $ASCII(tHashBytes, 2)
	Set tLZ = 1
	If tB1 = 0 {
		Set tLZ = 9
	} Else {
		While (tB1 # 2) = 0 { Set tLZ = tLZ + 1, tB1 = tB1 \ 2 }
	}
	If tLZ > $List(pHLL, tRegIdx) {
		Set $List(pHLL, tRegIdx) = tLZ
	}
}

/// Remove one timestamped edge, and everything InsertEdge derived from it.
///
/// The inverse of InsertEdge. Before this existed the only ways to remove a
/// temporal edge were time-based and coarse — Purge, PurgeBefore, PurgeRawBefore,
/// PurgeBucketRange — so removing one edge meant removing every other edge that
/// shared its window, and deleting a structural edge left its temporal entries
/// behind permanently.
///
/// Returns 0 when no edge existed at those coordinates, so a caller can tell a
/// delete from a no-op instead of treating both as success.
///
/// Three of the nine stores InsertEdge writes cannot be run backwards: min and max
/// are running extremes, and the distinct-count sketch is a HyperLogLog whose
/// registers only move up. Those are recomputed from the bucket's surviving edges
/// by RebuildBucketAggregate rather than adjusted. That is why this is one
/// classmethod and not a set of decrements a caller could compose: which stores
/// are invertible is not something the caller can know.
///
/// The structural shadow goes only when no temporal edge for this
/// (source, predicate, target) survives in this graph, because the shadow means
/// "this edge exists at some time".
ClassMethod DeleteTemporalEdge(graphId As %String = "", timestamp As %Integer, source As %String, predicate As %String, target As %String) As %Boolean
{
	Set tGKey = ##class(Graph.KG.GraphKey).ForIndex(graphId)
	Set tTS = +timestamp
	If '$Data(^KG("tout", tGKey, tTS, source, predicate, target)) { Quit 0 }
	Kill ^KG("tout", tGKey, tTS, source, predicate, target)
	Kill ^KG("tin", tGKey, tTS, target, predicate, source)
	Kill ^KG("edgeprop", tGKey, tTS, source, predicate, target)
	Set tBucketDiv = $Select(..#TSUNIT="ms": ..#BUCKETMS, 1: ..#BUCKET)
	Set tBucket = tTS \ tBucketDiv
	// A zero counter is not neutral: it still claims the source was active in this
	// bucket, which is what FindBursts and GetVelocity read.
	Set tCount = $Increment(^KG("bucket", tGKey, tBucket, source), -1)
	If tCount <= 0 { Kill ^KG("bucket", tGKey, tBucket, source) }
	Do ..RebuildBucketAggregate(tGKey, tBucket, source, predicate)
	If '..HasTemporalEdge(tGKey, source, predicate, target) {
		Do ##class(Graph.KG.EdgeScan).DeleteAdjacency(source, predicate, target, tGKey)
	}
	Quit 1
}

/// Recompute one bucket's count/sum/min/max/hll from the edges still in it.
///
/// Per-bucket is the right granularity because GetAggregate merges count and sum
/// and takes the extremes across every bucket in the requested range: a bucket
/// that is individually correct composes into a correct answer for any window.
///
/// count and sum could have been decremented. min, max and the sketch could not,
/// so recomputing all five keeps one rule ("the aggregate describes the surviving
/// edges") instead of two that can disagree.
ClassMethod RebuildBucketAggregate(pGraphKey, pBucket As %Integer, pSource As %String, pPredicate As %String) [ Private ]
{
	Set tBucketDiv = $Select(..#TSUNIT="ms": ..#BUCKETMS, 1: ..#BUCKET)
	Set tTSLow = pBucket * tBucketDiv
	Set tTSHigh = tTSLow + tBucketDiv - 1
	Set tCount = 0, tSum = 0, tMin = "", tMax = ""
	Set tHLL = ..HLLEmpty()
	Set tTS = tTSLow - 1
	For {
		Set tTS = $Order(^KG("tout", pGraphKey, tTS))
		Quit:(tTS = "")||(tTS > tTSHigh)
		Set tO = ""
		For {
			Set tO = $Order(^KG("tout", pGraphKey, tTS, pSource, pPredicate, tO), 1, tW)
			Quit:tO=""
			Set tCount = tCount + 1
			Set tSum = tSum + tW
			If (tMin = "") || (+tW < tMin) { Set tMin = +tW }
			If (tMax = "") || (+tW > tMax) { Set tMax = +tW }
			Do ..HLLAdd(.tHLL, tO)
		}
	}
	If tCount = 0 {
		Kill ^KG("tagg", pGraphKey, pBucket, pSource, pPredicate)
		Quit
	}
	Set ^KG("tagg", pGraphKey, pBucket, pSource, pPredicate, "count") = tCount
	Set ^KG("tagg", pGraphKey, pBucket, pSource, pPredicate, "sum") = tSum
	Set ^KG("tagg", pGraphKey, pBucket, pSource, pPredicate, "min") = tMin
	Set ^KG("tagg", pGraphKey, pBucket, pSource, pPredicate, "max") = tMax
	Set ^KG("tagg", pGraphKey, pBucket, pSource, pPredicate, "hll") = tHLL
}

/// Does any timestamp still carry this edge in this graph?
///
/// Walks every timestamp in the graph, because ^KG("tout") is keyed
/// (graph, ts, s, p, o) and there is no (s, p, o) -> ts index. The cost is one
/// $Order step per distinct timestamp, paid only on delete. Putting the graph key
/// first is what keeps the walk inside one graph.
ClassMethod HasTemporalEdge(pGraphKey, pSource As %String, pPredicate As %String, pTarget As %String) As %Boolean [ Private ]
{
	Set tFound = 0
	Set tTS = ""
	For {
		Set tTS = $Order(^KG("tout", pGraphKey, tTS))
		Quit:tTS=""
		If $Data(^KG("tout", pGraphKey, tTS, pSource, pPredicate, pTarget)) {
			Set tFound = 1
			Quit
		}
	}
	Quit tFound
}

/// Count distinct target nodes for source+predicate in a time window via HLL sketch.
/// When source="" (empty string), counts across ALL sources matching the predicate.
/// HLL registers are merged element-wise across all matching (source, predicate) pairs.
/// Same ~26% error rate as per-source mode.
ClassMethod GetDistinctCount(graphId As %String = "", source As %String, predicate As %String, tsStart As %Integer, tsEnd As %Integer) As %Integer
{
	Set tGKey = ##class(Graph.KG.GraphKey).ForIndex(graphId)
	Set tsStart = +tsStart
	Set tsEnd = +tsEnd
	Set tBucketDiv = $Select(..#TSUNIT="ms": ..#BUCKETMS, 1: ..#BUCKET)
	Set startBucket = tsStart \ tBucketDiv
	Set endBucket = tsEnd \ tBucketDiv
	Set merged = $ListBuild(0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0)
	For b = startBucket:1:endBucket {
		If source '= "" {
			Set hll = $Get(^KG("tagg", tGKey, b, source, predicate, "hll"))
			If hll = "" Continue
			For i = 1:1:16 {
				If $List(hll, i) > $List(merged, i) {
					Set $List(merged, i) = $List(hll, i)
				}
			}
		} Else {
			Set src = ""
			For {
				Set src = $Order(^KG("tagg", tGKey, b, src))
				Quit:(src = "")
				Set p = ""
				For {
					Set p = $Order(^KG("tagg", tGKey, b, src, p))
					Quit:(p = "")
					If predicate '= "" && (p '= predicate) { Continue }
					Set hll = $Get(^KG("tagg", tGKey, b, src, p, "hll"))
					If hll = "" Continue
					For i = 1:1:16 {
						If $List(hll, i) > $List(merged, i) {
							Set $List(merged, i) = $List(hll, i)
						}
					}
				}
			}
		}
	}
	// HarmonicMean estimator: alpha_16 * m^2 / Z where Z = sum(2^(-reg[i]))
	// alpha_16 = 0.673 for m=16 registers
	Set Z = 0
	For i = 1:1:16 {
		Set Z = Z + (1 / (2 ** $List(merged, i)))
	}
	If Z = 0 Return 0
	Set estimate = 0.673 * 16 * 16 / Z
	Return $Number(estimate, 0)
}

ClassMethod QueryWindow(graphId As %String = "", source As %String, predicate As %String, tsStart As %Integer, tsEnd As %Integer) As %String
{
	Set tGKey = ##class(Graph.KG.GraphKey).ForIndex(graphId)
	Set tsStart = +tsStart
	Set tsEnd = +tsEnd
	Set result = "["
	Set first = 1
	Set ts = tsStart - 1
	While 1 {
		Set ts = $Order(^KG("tout", tGKey, ts))
		If ts = "" Quit
		If ts > tsEnd Quit
		Set s = ""
		While 1 {
			Set s = $Order(^KG("tout", tGKey, ts, s))
			If s = "" Quit
			If source '= "" { If s '= source { Continue } }
			Set p = ""
			While 1 {
				Set p = $Order(^KG("tout", tGKey, ts, s, p))
				If p = "" Quit
				If predicate '= "" { If p '= predicate { Continue } }
				Set o = ""
				While 1 {
					Set o = $Order(^KG("tout", tGKey, ts, s, p, o), 1, w)
					If o = "" Quit
					If 'first Set result = result _ ","
					Set first = 0
					Set result = result _ "{""s"":""" _ ..jsonEsc(s) _ """,""p"":""" _ ..jsonEsc(p) _ """,""o"":""" _ ..jsonEsc(o) _ """,""ts"":" _ ts _ ",""w"":" _ ..jsonNum(w) _ "}"
				}
			}
		}
	}
	Return result _ "]"
}

ClassMethod QueryWindowSources(graphId As %String = "", predicate As %String, tsStart As %Integer, tsEnd As %Integer) As %String
{
	Set tGKey = ##class(Graph.KG.GraphKey).ForIndex(graphId)
	Set tsStart = +tsStart
	Set tsEnd = +tsEnd
	Kill dedup
	Set ts = tsStart - 1
	While 1 {
		Set ts = $Order(^KG("tout", tGKey, ts))
		If ts = "" Quit
		If ts > tsEnd Quit
		Set s = ""
		While 1 {
			Set s = $Order(^KG("tout", tGKey, ts, s))
			If s = "" Quit
			If predicate = "" {
				Set dedup(s) = 1
			} Else {
				If $Data(^KG("tout", tGKey, ts, s, predicate)) Set dedup(s) = 1
			}
		}
	}
	Set result = "[", first = 1, src = ""
	For {
		Set src = $Order(dedup(src))
		Quit:(src = "")
		If 'first { Set result = result _ "," }
		Set first = 0
		Set result = result _ """" _ ..jsonEsc(src) _ """"
	}
	Return result _ "]"
}

ClassMethod GetVelocity(graphId As %String = "", nodeId As %String, windowSec As %Integer = 300, nowTs As %Integer = 0) As %Integer
{
	Set tGKey = ##class(Graph.KG.GraphKey).ForIndex(graphId)
	Set now = $Select(nowTs > 0: nowTs,
		1: ##class(%SYSTEM.SQL).DATEDIFF("ss", "1970-01-01 00:00:00", $ZDateTime($ZTimeStamp, 3, 1)))
	Set tBucketDiv = $Select(..#TSUNIT="ms": ..#BUCKETMS, 1: ..#BUCKET)
	Set startBucket = (now - windowSec) \ tBucketDiv
	Set endBucket = now \ tBucketDiv
	Set total = 0
	For b = startBucket:1:endBucket {
		Set total = total + $Get(^KG("bucket", tGKey, b, nodeId), 0)
	}
	Return total
}

ClassMethod FindBursts(graphId As %String = "", predicate As %String, windowSec As %Integer, threshold As %Integer, nowTs As %Integer = 0) As %String
{
	Set tGKey = ##class(Graph.KG.GraphKey).ForIndex(graphId)
	Set now = $Select(nowTs > 0: nowTs,
		1: ##class(%SYSTEM.SQL).DATEDIFF("ss", "1970-01-01 00:00:00", $ZDateTime($ZTimeStamp, 3, 1)))
	Set tBucketDiv = $Select(..#TSUNIT="ms": ..#BUCKETMS, 1: ..#BUCKET)
	Set startBucket = (now - windowSec) \ tBucketDiv
	Set endBucket = now \ tBucketDiv
	Set result = "["
	Set first = 1
	Set b = startBucket - 1
	For {
		Set b = $Order(^KG("bucket", tGKey, b))
		Quit:b=""
		If b > endBucket Quit
		Set node = ""
		For {
			Set node = $Order(^KG("bucket", tGKey, b, node), 1, cnt)
			Quit:node=""
			If '$Data(totals(node)) Set totals(node) = 0
			Set totals(node) = totals(node) + cnt
		}
	}
	Set node = ""
	For {
		Set node = $Order(totals(node), 1, total)
		Quit:node=""
		If total >= threshold {
			Set:'first result = result _ ","
			Set first = 0
			Set result = result _ "{""id"":""" _ ..jsonEsc(node) _ """,""velocity"":" _ ..jsonNum(total) _ "}"
		}
	}
	Return result _ "]"
}

ClassMethod Purge()
{
	Kill ^KG("tout")
	Kill ^KG("tin")
	Kill ^KG("bucket")
	Kill ^KG("edgeprop")
	Kill ^KG("tagg")
}

/// Legacy method — purges raw edges and expired aggregate buckets for default graph.
ClassMethod PurgeBefore(tsEnd As %Integer)
{
	Set tGKey = 0  // default graph
	Set tsEnd = +tsEnd
	Set tBucketDiv = $Select(..#TSUNIT="ms": ..#BUCKETMS, 1: ..#BUCKET)
	Set maxSafeBucket = (tsEnd \ tBucketDiv) - 1
	Set ts = ""
	For {
		Set ts = $Order(^KG("tout", tGKey, ts))
		Quit:(ts = "")!(ts >= tsEnd)
		Set s = ""
		For {
			Set s = $Order(^KG("tout", tGKey, ts, s))
			Quit:s=""
			Set p = ""
			For {
				Set p = $Order(^KG("tout", tGKey, ts, s, p))
				Quit:p=""
				Set o = ""
				For {
					Set o = $Order(^KG("tout", tGKey, ts, s, p, o))
					Quit:o=""
					Kill ^KG("tout", tGKey, ts, s, p, o)
					Kill ^KG("tin",  tGKey, ts, o, p, s)
					Kill ^KG("edgeprop", tGKey, ts, s, p, o)
				}
			}
		}
		Set bucket = ts \ tBucketDiv
		If bucket <= maxSafeBucket {
			Kill ^KG("tagg", tGKey, bucket)
			Kill ^KG("bucket", tGKey, bucket)
		}
		Kill ^KG("tout", tGKey, ts)
	}
}

/// Delete raw temporal edges in [tsStart, tsEnd) for the specified graph.
/// graphId="" targets the default graph (key 0). tsStart=0 default deletes all before tsEnd.
/// Returns "deleted:skipped". ^KG("labelset") is never touched.
ClassMethod PurgeRawBefore(graphId As %String = "", tsEnd As %Integer, tsStart As %Integer = 0) As %String
{
	Set tGKey = ##class(Graph.KG.GraphKey).ForIndex(graphId)
	Set tsEnd = +tsEnd
	Set tsStart = $Select(+tsStart < 0: 0, 1: +tsStart)
	Set deleted = 0
	Set skipped = 0
	Set ts = ""
	For {
		Set ts = $Order(^KG("tout", tGKey, ts))
		Quit:(ts = "")!(ts >= tsEnd)
		If ts < tsStart {
			Set s = ""
			For {
				Set s = $Order(^KG("tout", tGKey, ts, s))
				Quit:s=""
				Set p = ""
				For {
					Set p = $Order(^KG("tout", tGKey, ts, s, p))
					Quit:p=""
					Set o = ""
					For {
						Set o = $Order(^KG("tout", tGKey, ts, s, p, o))
						Quit:o=""
						Set skipped = skipped + 1
					}
				}
			}
			Continue
		}
		Set s = ""
		For {
			Set s = $Order(^KG("tout", tGKey, ts, s))
			Quit:s=""
			Set p = ""
			For {
				Set p = $Order(^KG("tout", tGKey, ts, s, p))
				Quit:p=""
				Set o = ""
				For {
					Set o = $Order(^KG("tout", tGKey, ts, s, p, o))
					Quit:o=""
					Kill ^KG("tout", tGKey, ts, s, p, o)
					Kill ^KG("tin",  tGKey, ts, o, p, s)
					Kill ^KG("edgeprop", tGKey, ts, s, p, o)
					Set deleted = deleted + 1
				}
			}
		}
		Kill ^KG("tout", tGKey, ts)
	}
	Return deleted _ ":" _ skipped
}

/// Delete ^KG("tagg") and ^KG("bucket") entries in [bucketStart, bucketEnd] for one graph.
/// Raw edges (tout/tin/edgeprop) are never touched. Returns count of buckets removed.
ClassMethod PurgeBucketRange(graphId As %String = "", bucketStart As %Integer, bucketEnd As %Integer) As %Integer
{
	Set tGKey = ##class(Graph.KG.GraphKey).ForIndex(graphId)
	Set bucketStart = +bucketStart, bucketEnd = +bucketEnd
	Set tCount = 0
	If bucketStart > bucketEnd Quit 0
	Set bucket = bucketStart - 1
	For {
		Set bucket = $Order(^KG("tagg", tGKey, bucket))
		Quit:(bucket="")!(bucket > bucketEnd)
		Kill ^KG("tagg", tGKey, bucket)
		Kill ^KG("bucket", tGKey, bucket)
		Set tCount = tCount + 1
	}
	Quit tCount
}

/// Move a pre-spec-223 temporal index into the graph-scoped layout.
///
/// Every flat entry belongs to the default graph — there was nowhere else for it
/// to have come from — so ^KG("tout", ts, s, p, o) becomes
/// ^KG("tout", 0, ts, s, p, o), and the same for "tin", "edgeprop", "bucket" and
/// "tagg". Idempotent: a database already in the scoped layout has nothing flat
/// to find and returns 0.
///
/// FLAT IS A DEPTH, NOT A VALUE. A flat "tout" timestamp has (s, p, o) below it
/// and a graph key has (ts, s, p, o), so the two are told apart by the depth of
/// the tree below them. The first version of this method guessed from the
/// subscript instead — above 1000000, so it looks like a Unix timestamp — and
/// stopped its walk at anything that did not look like one. Integer 0 keys the
/// default graph and collates before every timestamp, so a single scoped
/// default-graph write was enough to stop the walk on its first step, and it
/// returned 0 while doing so, which is what an already-migrated database
/// returns.
///
/// That guard never ran at all, either: ObjectScript has no operator precedence,
/// so "+ts = ts && ts > 1000000" evaluates left to right as
/// ((+ts = ts) && ts) > 1000000, which is 1 > 1000000. Every call took the Else
/// branch and quit on the first subscript it saw. The migration had never moved
/// an entry.
///
/// ONE TRANSACTION. Every reader is graph-scoped, so a half-migrated index has
/// window queries answering from one layout while the rest of the data sits at
/// the other. The batched TCOMMIT is therefore gone: this either finishes or
/// leaves the flat layout exactly as it was, and throws rather than returning a
/// count it did not achieve (the same contract as Graph.KG.Eraser — ADR-0004).
///
/// Returns the number of raw "tout" entries moved.
ClassMethod MigrateToGraphScoped() As %Integer
{
	Set tMigrated = 0
	Try {
		TSTART
		Set tMigrated = ..MigrateRaw()
		Do ..MigrateDerived()
		TCOMMIT
	} Catch tEx {
		If $TLevel > 0 { TROLLBACK }
		Throw tEx
	}
	Quit tMigrated
}

/// Move the raw trees: "tout", "tin", and the "edgeprop" entries hanging off them.
ClassMethod MigrateRaw() As %Integer [ Private ]
{
	Set tMigrated = 0

	// Collect the flat first subscripts before moving anything: a relocation
	// writes into the same global this walks.
	Kill tFlat
	Set tKey = ""
	For {
		Set tKey = $Order(^KG("tout", tKey))
		Quit:tKey=""
		If ..FlatDepth("tout", tKey) = 3 { Set tFlat(tKey) = "" }
	}

	Set tTs = ""
	For {
		Set tTs = $Order(tFlat(tTs))
		Quit:tTs=""
		Set tS = ""
		For {
			Set tS = $Order(^KG("tout", tTs, tS))
			Quit:tS=""
			Set tP = ""
			For {
				Set tP = $Order(^KG("tout", tTs, tS, tP))
				Quit:tP=""
				Set tO = ""
				For {
					Set tO = $Order(^KG("tout", tTs, tS, tP, tO), 1, tW)
					Quit:tO=""
					// A scoped entry at the same coordinates wins: it is the one
					// every reader can already see. The flat duplicate goes
					// either way, so a second run has nothing left to trip over
					// and the count means "moved", not "looked at".
					If '$Data(^KG("tout", 0, tTs, tS, tP, tO)) {
						Set ^KG("tout", 0, tTs, tS, tP, tO) = tW
						Set ^KG("tin", 0, tTs, tO, tP, tS) = tW
						Set tK = ""
						For {
							Set tK = $Order(^KG("edgeprop", tTs, tS, tP, tO, tK), 1, tV)
							Quit:tK=""
							Set ^KG("edgeprop", 0, tTs, tS, tP, tO, tK) = tV
						}
						Set tMigrated = tMigrated + 1
					}
					Kill ^KG("edgeprop", tTs, tS, tP, tO)
					Kill ^KG("tin", tTs, tO, tP, tS)
				}
			}
		}
		Kill ^KG("tout", tTs)
	}

	// "tin" and "edgeprop" entries whose "tout" is already gone. InsertEdge can
	// suppress the reverse index, and a purge removes raw entries — so the two
	// trees are not guaranteed to line up, and an entry left at a layout no
	// reader looks at is the drift this migration exists to end.
	Do ..MigrateOrphans("tin", 3)
	Do ..MigrateOrphans("edgeprop", 4)
	Quit tMigrated
}

/// Relocate whatever is left at a flat first subscript of one store.
ClassMethod MigrateOrphans(pStore As %String, pFlatDepth As %Integer) [ Private ]
{
	Kill tFlat
	Set tKey = ""
	For {
		Set tKey = $Order(^KG(pStore, tKey))
		Quit:tKey=""
		If ..FlatDepth(pStore, tKey) = pFlatDepth { Set tFlat(tKey) = "" }
	}

	Set tKey = ""
	For {
		Set tKey = $Order(tFlat(tKey))
		Quit:tKey=""
		Set tFrom = $Name(^KG(pStore, tKey))
		Set tTo = $Name(^KG(pStore, 0, tKey))
		Merge @tTo = @tFrom
		Kill @tFrom
	}
	Quit
}

/// Move "bucket" and "tagg" into the scoped layout.
///
/// A bucket present at only one layout is relocated whole, because that is exact
/// — and because recomputing it from raw entries would delete the aggregates
/// PurgeRawBefore deliberately keeps after dropping their raw edges (13-month
/// retention).
///
/// A bucket present at both layouts is recomputed from the scoped raw entries
/// instead, because two aggregates for one bucket cannot be combined: "count"
/// and "sum" would add and "min"/"max" would fold, but the HLL sketch is an
/// opaque register string with no merge available here. A recomputed bucket
/// loses whatever contributions had already been purged out of raw, which is the
/// price of the collision and the reason relocation is preferred.
ClassMethod MigrateDerived() [ Private ]
{
	// One list from both trees: a bucket can have a counter with no aggregate,
	// or an aggregate whose raw edges were purged, and either has to move.
	Kill tFlat
	Set tKey = ""
	For {
		Set tKey = $Order(^KG("bucket", tKey))
		Quit:tKey=""
		If ..FlatDepth("bucket", tKey) = 1 { Set tFlat(tKey) = "" }
	}
	Set tKey = ""
	For {
		Set tKey = $Order(^KG("tagg", tKey))
		Quit:tKey=""
		If ..FlatDepth("tagg", tKey) = 3 { Set tFlat(tKey) = "" }
	}

	Set tBucket = ""
	For {
		Set tBucket = $Order(tFlat(tBucket))
		Quit:tBucket=""
		If $Data(^KG("bucket", 0, tBucket)) || $Data(^KG("tagg", 0, tBucket)) {
			Kill ^KG("bucket", tBucket)
			Kill ^KG("tagg", tBucket)
			Do ..RebuildBucket(0, tBucket)
		} Else {
			If $Data(^KG("bucket", tBucket)) {
				Merge ^KG("bucket", 0, tBucket) = ^KG("bucket", tBucket)
				Kill ^KG("bucket", tBucket)
			}
			If $Data(^KG("tagg", tBucket)) {
				Merge ^KG("tagg", 0, tBucket) = ^KG("tagg", tBucket)
				Kill ^KG("tagg", tBucket)
			}
		}
	}
	Quit
}

/// Derive one bucket's counters and aggregates again from the raw entries in it.
///
/// Through the same $Increment sequence and the same UpdateHLL the writer uses,
/// so a rebuilt bucket and a written one cannot disagree about how they were
/// built.
ClassMethod RebuildBucket(pGKey As %String, pBucket As %Integer) [ Private ]
{
	Set tDiv = $Select(..#TSUNIT="ms": ..#BUCKETMS, 1: ..#BUCKET)
	Set tFrom = pBucket * tDiv
	Set tTo = tFrom + tDiv - 1
	Kill ^KG("bucket", pGKey, pBucket)
	Kill ^KG("tagg", pGKey, pBucket)

	Set tTs = tFrom - 1
	For {
		Set tTs = $Order(^KG("tout", pGKey, tTs))
		Quit:tTs=""
		Quit:tTs>tTo
		Set tS = ""
		For {
			Set tS = $Order(^KG("tout", pGKey, tTs, tS))
			Quit:tS=""
			Set tP = ""
			For {
				Set tP = $Order(^KG("tout", pGKey, tTs, tS, tP))
				Quit:tP=""
				Set tO = ""
				For {
					Set tO = $Order(^KG("tout", pGKey, tTs, tS, tP, tO), 1, tW)
					Quit:tO=""
					Set tmp = $Increment(^KG("bucket", pGKey, pBucket, tS))
					Set tmp = $Increment(^KG("tagg", pGKey, pBucket, tS, tP, "count"))
					Set tmp = $Increment(^KG("tagg", pGKey, pBucket, tS, tP, "sum"), tW)
					If '$Data(^KG("tagg", pGKey, pBucket, tS, tP, "min")) || (tW < ^KG("tagg", pGKey, pBucket, tS, tP, "min")) {
						Set ^KG("tagg", pGKey, pBucket, tS, tP, "min") = tW
					}
					If '$Data(^KG("tagg", pGKey, pBucket, tS, tP, "max")) || (tW > ^KG("tagg", pGKey, pBucket, tS, tP, "max")) {
						Set ^KG("tagg", pGKey, pBucket, tS, tP, "max") = tW
					}
					Do ..UpdateHLL(pGKey, pBucket, tS, tP, tO)
				}
			}
		}
	}
	Quit
}

/// The depth of the tree below ^KG(pStore, pKey), in subscripts.
///
/// This is what tells a flat subscript from a graph key, and it is a fact about
/// the layout rather than a guess about what a subscript looks like: a flat
/// "tout" timestamp has (s, p, o) below it, a graph key has (ts, s, p, o).
///
/// The first and the last child are both probed. A disagreement means one
/// subscript holds both layouts — a flat bucket 0 sitting under the same node as
/// the default graph's key 0, say — which depth cannot resolve, so it throws
/// rather than migrating half of it.
ClassMethod FlatDepth(pStore As %String, pKey As %String) As %Integer [ Private ]
{
	Set tRef = $Name(^KG(pStore, pKey))
	Set tFirst = ..LeafDepth(tRef, 1)
	Set tLast = ..LeafDepth(tRef, -1)
	If tFirst '= tLast {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "^KG(""" _ pStore _ """, " _ pKey _ ") holds both the flat and the graph-scoped layout at once; nothing was migrated"))
	}
	Quit tFirst
}

/// Walk one edge of the tree below pRef and return how deep it goes.
ClassMethod LeafDepth(pRef As %String, pDir As %Integer = 1) As %Integer [ Private ]
{
	Set tDepth = 0
	Set tRef = pRef
	For {
		Set tChild = $Order(@tRef@(""), pDir)
		Quit:tChild=""
		Set tRef = $Name(@tRef@(tChild))
		Set tDepth = tDepth + 1
	}
	Quit tDepth
}

/// Intern an attribute JSON object: canonicalize, hash, store once, return SHA1 hex.
/// Returns "" on invalid JSON input. Idempotent.
/// Type fidelity: numbers intern as numbers, booleans as booleans, nulls as null.
/// ^KG("labelset") is append-only and NEVER touched by PurgeRawBefore or PurgeBefore.
/// Interned label sets outlive raw edges -- safe for 13-month aggregate retention.
ClassMethod InternLabelSet(attrsJSON As %String) As %String
{
	Set tSC = $$$OK
	Set obj = ##class(%DynamicObject).%FromJSON(attrsJSON)
	If '$IsObject(obj) { Return "" }
	// Collect keys into a local array for sorting
	Set iter = obj.%GetIterator()
	Kill keys
	Set keyCount = 0
	While iter.%GetNext(.key) {
		Set keyCount = keyCount + 1
		Set keys(key) = ""
	}
	// Build canonical JSON with sorted keys
	Set canonical = "{"
	Set first = 1
	Set k = ""
	For {
		Set k = $Order(keys(k))
		Quit:(k = "")
		Set val = obj.%Get(k)
		Set valType = obj.%GetTypeOf(k)
		If 'first { Set canonical = canonical _ "," }
		Set first = 0
		// Encode key
		Set canonical = canonical _ """" _ k _ """:"
		// Encode value based on type
		If valType = "number" {
			Set canonical = canonical _ val
		} ElseIf valType = "boolean" {
			Set canonical = canonical _ $Select(val:"true", 1:"false")
		} ElseIf valType = "null" {
			Set canonical = canonical _ "null"
		} Else {
			Set canonical = canonical _ """" _ val _ """"
		}
	}
	Set canonical = canonical _ "}"
	// SHA1 hash -> lowercase hex
	Set hashBytes = $SYSTEM.Encryption.SHA1Hash(canonical)
	Set hex = ""
	For i = 1:1:$Length(hashBytes) {
		Set b = $ASCII(hashBytes, i)
		Set hex = hex _ $Extract("0123456789abcdef", (b \ 16) + 1) _ $Extract("0123456789abcdef", (b # 16) + 1)
	}
	// Store if absent
	If '$Data(^KG("labelset", hex)) {
		Set ^KG("labelset", hex) = canonical
	}
	Return hex
}

/// Return the canonical JSON for a known label set hash. Returns "" if unknown.
ClassMethod ResolveLabelSet(hash As %String) As %String
{
	Return $Get(^KG("labelset", hash), "")
}

/// WARNING: Edges written with suppressReverseIndex=1 (or "sri":1 in BulkInsert)
/// are NOT visible via this method. Those edges write only to ^KG("tout") and have
/// no ^KG("tin") entry. This is intentional. Callers mixing suppressed and
/// non-suppressed edges will see only non-suppressed edges here.
ClassMethod QueryWindowInbound(graphId As %String = "", target As %String, predicate As %String, tsStart As %Integer, tsEnd As %Integer) As %String
{
	Set tGKey = ##class(Graph.KG.GraphKey).ForIndex(graphId)
	Set tsStart = +tsStart
	Set tsEnd = +tsEnd
	Set result = "["
	Set first = 1
	Set ts = tsStart - 1
	While 1 {
		Set ts = $Order(^KG("tin", tGKey, ts))
		If ts = "" Quit
		If ts > tsEnd Quit
		Set o = ""
		While 1 {
			Set o = $Order(^KG("tin", tGKey, ts, o))
			If o = "" Quit
			If target '= "" { If o '= target { Continue } }
			Set p = ""
			While 1 {
				Set p = $Order(^KG("tin", tGKey, ts, o, p))
				If p = "" Quit
				If predicate '= "" { If p '= predicate { Continue } }
				Set s = ""
				While 1 {
					Set s = $Order(^KG("tin", tGKey, ts, o, p, s), 1, w)
					If s = "" Quit
					If 'first Set result = result _ ","
					Set first = 0
					Set result = result _ "{""s"":""" _ ..jsonEsc(s) _ """,""p"":""" _ ..jsonEsc(p) _ """,""o"":""" _ ..jsonEsc(o) _ """,""ts"":" _ ts _ ",""w"":" _ ..jsonNum(w) _ "}"
				}
			}
		}
	}
	Return result _ "]"
}

ClassMethod GetAggregate(graphId As %String = "", source As %String, predicate As %String, metric As %String, tsStart As %Integer, tsEnd As %Integer) As %String
{
	Set tGKey = ##class(Graph.KG.GraphKey).ForIndex(graphId)
	Set tsStart = +tsStart
	Set tsEnd = +tsEnd
	Set tBucketDiv = $Select(..#TSUNIT="ms": ..#BUCKETMS, 1: ..#BUCKET)
	Set startBucket = tsStart \ tBucketDiv
	Set endBucket = tsEnd \ tBucketDiv
	Set totalCount = 0, totalSum = 0
	Set globalMin = "", globalMax = ""
	For b = startBucket:1:endBucket {
		Set cnt = +$Get(^KG("tagg", tGKey, b, source, predicate, "count"), 0)
		Set sm = +$Get(^KG("tagg", tGKey, b, source, predicate, "sum"), 0)
		Set mn = $Get(^KG("tagg", tGKey, b, source, predicate, "min"))
		Set mx = $Get(^KG("tagg", tGKey, b, source, predicate, "max"))
		Set totalCount = totalCount + cnt
		Set totalSum = totalSum + sm
		If mn '= "" { If globalMin = "" || (mn < globalMin) Set globalMin = mn }
		If mx '= "" { If globalMax = "" || (mx > globalMax) Set globalMax = mx }
	}
	If metric = "count" Return totalCount
	If metric = "sum" Return totalSum
	If metric = "min" Return $Select(globalMin="":"", 1:globalMin)
	If metric = "max" Return $Select(globalMax="":"", 1:globalMax)
	If metric = "avg" Return $Select(totalCount=0:"", 1:$FN(totalSum/totalCount,"",6))
	Return ""
}

ClassMethod GetBucketGroups(graphId As %String = "", predicate As %String, tsStart As %Integer, tsEnd As %Integer, sourcePrefix As %String = "") As %String
{
	Set tGKey = ##class(Graph.KG.GraphKey).ForIndex(graphId)
	Set tsStart = +tsStart
	Set tsEnd = +tsEnd
	Set tBucketDiv = $Select(..#TSUNIT="ms": ..#BUCKETMS, 1: ..#BUCKET)
	Set startBucket = tsStart \ tBucketDiv
	Set endBucket = tsEnd \ tBucketDiv
	Set prefixLen = $Length(sourcePrefix)
	Kill totals
	Set b = startBucket - 1
	For {
		Set b = b + 1
		Quit:(b > endBucket)
		Set src = ""
		For {
			Set src = $Order(^KG("tagg", tGKey, b, src))
			Quit:(src = "")
			If prefixLen > 0 && ($Extract(src, 1, prefixLen) '= sourcePrefix) { Continue }
			Set p = ""
			For {
				Set p = $Order(^KG("tagg", tGKey, b, src, p))
				Quit:(p = "")
				If predicate '= "" && (p '= predicate) { Continue }
				Set cnt = +$Get(^KG("tagg", tGKey, b, src, p, "count"), 0)
				Set sm = +$Get(^KG("tagg", tGKey, b, src, p, "sum"), 0)
				Set mn = $Get(^KG("tagg", tGKey, b, src, p, "min"))
				Set mx = $Get(^KG("tagg", tGKey, b, src, p, "max"))
				If '$Data(totals(src, p, "count")) { Set totals(src, p, "count") = 0, totals(src, p, "sum") = 0, totals(src, p, "min") = "", totals(src, p, "max") = "" }
				Set totals(src, p, "count") = totals(src, p, "count") + cnt
				Set totals(src, p, "sum") = totals(src, p, "sum") + sm
				If mn '= "" { If totals(src, p, "min") = "" || (mn < totals(src, p, "min")) Set totals(src, p, "min") = mn }
				If mx '= "" { If totals(src, p, "max") = "" || (mx > totals(src, p, "max")) Set totals(src, p, "max") = mx }
			}
		}
	}
	Set result = "[", first = 1
	Set src = ""
	For {
		Set src = $Order(totals(src))
		Quit:(src = "")
		Set p = ""
		For {
			Set p = $Order(totals(src, p))
			Quit:(p = "")
			Set cnt = totals(src, p, "count")
			Set sm = totals(src, p, "sum")
			Set mn = $Select(totals(src, p, "min")="":"null", 1:totals(src, p, "min"))
			Set mx = $Select(totals(src, p, "max")="":"null", 1:totals(src, p, "max"))
			Set avg = $Select(cnt=0:"null", 1:$FN(sm/cnt,"",6))
			If 'first Set result = result _ ","
			Set first = 0
			Set result = result _ "{""source"":""" _ ..jsonEsc(src) _ """,""predicate"":""" _ ..jsonEsc(p) _ """,""count"":" _ ..jsonNum(cnt) _ ",""sum"":" _ ..jsonNum(sm) _ ",""avg"":" _ ..jsonNum(avg) _ ",""min"":" _ ..jsonNum(mn) _ ",""max"":" _ ..jsonNum(mx) _ "}"
		}
	}
	Return result _ "]"
}

ClassMethod GetBucketGroupTargets(graphId As %String = "", source As %String, predicate As %String, tsStart As %Integer, tsEnd As %Integer) As %String
{
	Set tGKey = ##class(Graph.KG.GraphKey).ForIndex(graphId)
	Set tsStart = +tsStart
	Set tsEnd = +tsEnd
	Set ts = tsStart - 1
	Kill dedup
	For {
		Set ts = $Order(^KG("tout", tGKey, ts))
		Quit:(ts = "")
		If ts > tsEnd Quit
		If $Data(^KG("tout", tGKey, ts, source)) {
			Set p = ""
			For {
				Set p = $Order(^KG("tout", tGKey, ts, source, p))
				Quit:(p = "")
				If predicate '= "" && (p '= predicate) { Continue }
				Set o = ""
				For {
					Set o = $Order(^KG("tout", tGKey, ts, source, p, o))
					Quit:(o = "")
					Set dedup(o) = 1
				}
			}
		}
	}
	Set result = "[", first = 1, tgt = ""
	For {
		Set tgt = $Order(dedup(tgt))
		Quit:(tgt = "")
		If 'first { Set result = result _ "," }
		Set first = 0
		Set result = result _ """" _ ..jsonEsc(tgt) _ """"
	}
	Return result _ "]"
}

ClassMethod GetEdgeAttrs(graphId As %String = "", ts As %Integer, source As %String, predicate As %String, target As %String) As %String
{
	Set tGKey = ##class(Graph.KG.GraphKey).ForIndex(graphId)
	Set ts = +ts
	Set result = ##class(%DynamicObject).%New()
	Set key = ""
	While 1 {
		Set key = $Order(^KG("edgeprop", tGKey, ts, source, predicate, target, key), 1, val)
		If key = "" Quit
		Do result.%Set(key, val)
	}
	Return result.%ToJSON()
}

}
