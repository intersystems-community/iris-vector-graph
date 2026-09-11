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
	Set tGKey = $Select(graphId = "": 0, 1: $ZStrip(graphId, "*C"))
	If timestamp = "" Set timestamp = ##class(%SYSTEM.SQL).DATEDIFF("ss", "1970-01-01 00:00:00", $ZDateTime($ZTimeStamp, 3, 1))
	// Coerce empty weight to 1 — same guard as the batch path.
	If weight = "" Set weight = 1
	// Resolve effective mode: explicit mode overrides legacy upsert boolean.
	If mode = "" { Set mode = $Select(upsert: "update", 1: "insert") }
	If mode = "skip" && $Data(^KG("tout", tGKey, timestamp, source, predicate, target)) { Return 1 }
	If mode = "update" {
		Kill ^KG("edgeprop", tGKey, timestamp, source, predicate, target)
	}
	Set ^KG("tout", tGKey, timestamp, source, predicate, target) = weight
	If 'suppressReverseIndex { Set ^KG("tin", tGKey, timestamp, target, predicate, source) = weight }
	Set tBucketDiv = $Select(..#TSUNIT="ms": ..#BUCKETMS, 1: ..#BUCKET)
	Set bucket = timestamp \ tBucketDiv
	Set tmp = $Increment(^KG("bucket", tGKey, bucket, source))
	// Structural adjacency shadow — always at default graph key 0 for BFS/traversal.
	Set ^KG("out", tGKey, source, predicate, target) = weight
	Set ^KG("in", tGKey, target, predicate, source) = weight
	Set tmp = $Increment(^KG("deg", tGKey, source))
	// count and sum are atomic via $Increment
	Set tmp = $Increment(^KG("tagg", tGKey, bucket, source, predicate, "count"))
	Set tmp = $Increment(^KG("tagg", tGKey, bucket, source, predicate, "sum"), weight)
	If '$Data(^KG("tagg", tGKey, bucket, source, predicate, "min")) || (weight < ^KG("tagg", tGKey, bucket, source, predicate, "min")) {
		Set ^KG("tagg", tGKey, bucket, source, predicate, "min") = weight
	}
	If '$Data(^KG("tagg", tGKey, bucket, source, predicate, "max")) || (weight > ^KG("tagg", tGKey, bucket, source, predicate, "max")) {
		Set ^KG("tagg", tGKey, bucket, source, predicate, "max") = weight
	}
	Do ..UpdateHLL(tGKey, bucket, source, predicate, target)
	If attrsJSON '= "" {
		Set attrs = ##class(%DynamicObject).%FromJSON(attrsJSON)
		Set iter = attrs.%GetIterator()
		While iter.%GetNext(.key, .val) {
			Set ^KG("edgeprop", tGKey, timestamp, source, predicate, target, key) = val
		}
	}
	Return 1
}

ClassMethod BulkInsert(graphId As %String = "", batchJSON As %String, upsert As %Boolean = 0) As %Integer
{
	Set tGKey = $Select(graphId = "": 0, 1: $ZStrip(graphId, "*C"))
	Set arr = ##class(%DynamicArray).%FromJSON(batchJSON)
	Set n = arr.%Size()
	Set count = 0
	For i = 0:1:(n-1) {
		Set item = arr.%Get(i)
		Set source = item.%Get("s")
		Set predicate = item.%Get("p")
		Set target = item.%Get("o")
		Set ts = +item.%Get("ts")
		Set weight = item.%Get("w")
		If weight = "" Set weight = 1
		If ts = 0 Set ts = ##class(%SYSTEM.SQL).DATEDIFF("ss", "1970-01-01 00:00:00", $ZDateTime($ZTimeStamp, 3, 1))
		If upsert && $Data(^KG("tout", tGKey, ts, source, predicate, target)) { Continue }
		Set suppR = +item.%Get("sri")
		Set ^KG("tout", tGKey, ts, source, predicate, target) = weight
		If 'suppR { Set ^KG("tin", tGKey, ts, target, predicate, source) = weight }
		Set tBucketDiv = $Select(..#TSUNIT="ms": ..#BUCKETMS, 1: ..#BUCKET)
		Set bucket = ts \ tBucketDiv
		Set tmp = $Increment(^KG("bucket", tGKey, bucket, source))
		Set ^KG("out", tGKey, source, predicate, target) = weight
		Set ^KG("in", tGKey, target, predicate, source) = weight
		Set tmp = $Increment(^KG("deg", tGKey, source))
		Set tmp = $Increment(^KG("tagg", tGKey, bucket, source, predicate, "count"))
		Set tmp = $Increment(^KG("tagg", tGKey, bucket, source, predicate, "sum"), weight)
		If '$Data(^KG("tagg", tGKey, bucket, source, predicate, "min")) || (weight < ^KG("tagg", tGKey, bucket, source, predicate, "min")) { Set ^KG("tagg", tGKey, bucket, source, predicate, "min") = weight }
		If '$Data(^KG("tagg", tGKey, bucket, source, predicate, "max")) || (weight > ^KG("tagg", tGKey, bucket, source, predicate, "max")) { Set ^KG("tagg", tGKey, bucket, source, predicate, "max") = weight }
		Do ..UpdateHLL(tGKey, bucket, source, predicate, target)
		Set attrs = item.%Get("attrs")
		If attrs '= "" && ($IsObject(attrs)) {
			Set iter = attrs.%GetIterator()
			While iter.%GetNext(.key, .val) {
				Set ^KG("edgeprop", tGKey, ts, source, predicate, target, key) = val
			}
		}
		Set count = count + 1
	}
	Return count
}

ClassMethod UpdateHLL(graphKey, bucket As %Integer, source As %String, predicate As %String, value As %String) [ Private ]
{
	// 16-register HLL sketch. SHA1 first byte -> register index, second byte -> leading-zero count.
	Set hashBytes = $SYSTEM.Encryption.SHA1Hash(value)
	Set regIdx = ($ASCII(hashBytes, 1) # 16) + 1
	Set b1 = $ASCII(hashBytes, 2)
	Set lz = 1
	If b1 = 0 {
		Set lz = 9
	} Else {
		While (b1 # 2) = 0 { Set lz = lz + 1, b1 = b1 \ 2 }
	}
	Set hll = $Get(^KG("tagg", graphKey, bucket, source, predicate, "hll"), $ListBuild(0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0))
	If lz > $List(hll, regIdx) {
		Set $List(hll, regIdx) = lz
		Set ^KG("tagg", graphKey, bucket, source, predicate, "hll") = hll
	}
}

/// Count distinct target nodes for source+predicate in a time window via HLL sketch.
/// When source="" (empty string), counts across ALL sources matching the predicate.
/// HLL registers are merged element-wise across all matching (source, predicate) pairs.
/// Same ~26% error rate as per-source mode.
ClassMethod GetDistinctCount(graphId As %String = "", source As %String, predicate As %String, tsStart As %Integer, tsEnd As %Integer) As %Integer
{
	Set tGKey = $Select(graphId = "": 0, 1: $ZStrip(graphId, "*C"))
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
	Set tGKey = $Select(graphId = "": 0, 1: $ZStrip(graphId, "*C"))
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
	Set tGKey = $Select(graphId = "": 0, 1: $ZStrip(graphId, "*C"))
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
	Set tGKey = $Select(graphId = "": 0, 1: $ZStrip(graphId, "*C"))
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
	Set tGKey = $Select(graphId = "": 0, 1: $ZStrip(graphId, "*C"))
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
	Set tGKey = $Select(graphId = "": 0, 1: $ZStrip(graphId, "*C"))
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
	Set tGKey = $Select(graphId = "": 0, 1: $ZStrip(graphId, "*C"))
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

/// Migrate flat (pre-spec-223) temporal globals to the graph-scoped layout.
/// All flat entries at ^KG("tout", ts, ...) are moved to ^KG("tout", 0, ts, ...) (default graph).
/// Idempotent: a second run returns 0 and leaves data unchanged.
/// Returns the count of tout entries migrated.
ClassMethod MigrateToGraphScoped() As %Integer
{
	Set migrated = 0
	Set batchSize = 10000
	Set batchCount = 0
	TStart
	Set ts = ""
	For {
		Set ts = $Order(^KG("tout", ts))
		// Stop when we hit a non-integer key (graph-scoped entries have string or int first subscript)
		// Flat entries have a timestamp integer as first subscript.
		// Graph-scoped entries have integer 0 or a string graph name.
		// We only process integer timestamps > 0 (flat entries).
		If ts = "" Quit
		If ts = 0 Quit  // already the graph key — scoped data, stop
		If '$IsValidNum(ts) Quit  // string graph key — scoped data, stop
		If +ts = ts && ts > 1000000 {
			// Looks like a Unix timestamp (>1M) — flat entry
			Set s = ""
			For {
				Set s = $Order(^KG("tout", ts, s))
				Quit:s=""
				If '$IsValidNum(s) {
					// s is a node ID string — this is a flat entry at (ts, s, ...)
					Set p = ""
					For {
						Set p = $Order(^KG("tout", ts, s, p))
						Quit:p=""
						Set o = ""
						For {
							Set o = $Order(^KG("tout", ts, s, p, o), 1, w)
							Quit:o=""
							// Write at new scoped location (default graph = 0)
							If '$Data(^KG("tout", 0, ts, s, p, o)) {
								Set ^KG("tout", 0, ts, s, p, o) = w
								Set ^KG("tin",  0, ts, o, p, s) = w
								// Migrate edgeprop
								Set k = ""
								For {
									Set k = $Order(^KG("edgeprop", ts, s, p, o, k), 1, v)
									Quit:k=""
									Set ^KG("edgeprop", 0, ts, s, p, o, k) = v
								}
								Kill ^KG("edgeprop", ts, s, p, o)
								Kill ^KG("tout", ts, s, p, o)
								Kill ^KG("tin",  ts, o, p, s)
								Set migrated = migrated + 1
								Set batchCount = batchCount + 1
								If batchCount >= batchSize { TCommit  TStart  Set batchCount = 0 }
							}
						}
					}
				}
			}
		} Else {
			Quit  // not a flat timestamp
		}
	}
	TCommit
	Return migrated
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
	Set tGKey = $Select(graphId = "": 0, 1: $ZStrip(graphId, "*C"))
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
	Set tGKey = $Select(graphId = "": 0, 1: $ZStrip(graphId, "*C"))
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
	Set tGKey = $Select(graphId = "": 0, 1: $ZStrip(graphId, "*C"))
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
	Set tGKey = $Select(graphId = "": 0, 1: $ZStrip(graphId, "*C"))
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
	Set tGKey = $Select(graphId = "": 0, 1: $ZStrip(graphId, "*C"))
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
