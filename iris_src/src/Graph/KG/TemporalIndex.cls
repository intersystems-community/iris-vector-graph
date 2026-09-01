Class Graph.KG.TemporalIndex Extends %RegisteredObject
{

Parameter BUCKET = 300;

/// Timestamp precision unit. "" = seconds (default). "ms" = milliseconds.
Parameter TSUNIT As %String = "";

/// Bucket width in milliseconds when TSUNIT="ms". Derived: BUCKET * 1000.
Parameter BUCKETMS As %Integer = 300000;

ClassMethod InsertEdge(source As %String, predicate As %String, target As %String, timestamp As %Integer = "", weight As %Double = 1.0, attrsJSON As %String = "", upsert As %Boolean = 0, suppressReverseIndex As %Boolean = 0) As %Status
{
	If timestamp = "" Set timestamp = ##class(%SYSTEM.SQL).DATEDIFF("ss", "1970-01-01 00:00:00", $ZDateTime($ZTimeStamp, 3, 1))
	If upsert && $Data(^KG("tout", timestamp, source, predicate, target)) { Return 1 }
	Set ^KG("tout", timestamp, source, predicate, target) = weight
	If 'suppressReverseIndex { Set ^KG("tin", timestamp, target, predicate, source) = weight }
	Set tBucketDiv = $Select(..#TSUNIT="ms": ..#BUCKETMS, 1: ..#BUCKET)
	Set bucket = timestamp \ tBucketDiv
	Set tmp = $Increment(^KG("bucket", bucket, source))
	Set ^KG("out", 0, source, predicate, target) = weight
	Set ^KG("in", 0, target, predicate, source) = weight
	Set tmp = $Increment(^KG("deg", source))
	// count and sum are atomic via $Increment
	Set tmp = $Increment(^KG("tagg", bucket, source, predicate, "count"))
	Set tmp = $Increment(^KG("tagg", bucket, source, predicate, "sum"), weight)
	// WARNING: min/max updates are NOT atomic under concurrent writes (read-modify-write race).
	// $Increment is atomic; conditional set is not. Under single-threaded ingest this is correct.
	// Phase 2 fix: wrap in LOCK ^KG("tagg", bucket, source, predicate) for concurrent safety.
	If '$Data(^KG("tagg", bucket, source, predicate, "min")) || (weight < ^KG("tagg", bucket, source, predicate, "min")) {
		Set ^KG("tagg", bucket, source, predicate, "min") = weight
	}
	If '$Data(^KG("tagg", bucket, source, predicate, "max")) || (weight > ^KG("tagg", bucket, source, predicate, "max")) {
		Set ^KG("tagg", bucket, source, predicate, "max") = weight
	}
	// HLL sketch for COUNT DISTINCT of target values per (bucket, source, predicate).
	// 16-register HLL using SHA1 hash. $SYSTEM.Encryption.SHA1Hash verified available in IRIS 2025.1.
	// ~26% error rate for 16 registers — acceptable for fanout burst detection thresholds.
	// HLL update is also NOT atomic under concurrent writes (same race as min/max above).
	Do ..UpdateHLL(bucket, source, predicate, target)
	If attrsJSON '= "" {
		Set attrs = ##class(%DynamicObject).%FromJSON(attrsJSON)
		Set iter = attrs.%GetIterator()
		While iter.%GetNext(.key, .val) {
			Set ^KG("edgeprop", timestamp, source, predicate, target, key) = val
		}
	}
	Return 1
}

ClassMethod BulkInsert(batchJSON As %String, upsert As %Boolean = 0) As %Integer
{
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
		If upsert && $Data(^KG("tout", ts, source, predicate, target)) { Continue }
		Set suppR = +item.%Get("suppress_reverse")
		Set ^KG("tout", ts, source, predicate, target) = weight
		If 'suppR { Set ^KG("tin", ts, target, predicate, source) = weight }
		Set tBucketDiv = $Select(..#TSUNIT="ms": ..#BUCKETMS, 1: ..#BUCKET)
		Set bucket = ts \ tBucketDiv
		Set tmp = $Increment(^KG("bucket", bucket, source))
		Set ^KG("out", 0, source, predicate, target) = weight
		Set ^KG("in", 0, target, predicate, source) = weight
		Set tmp = $Increment(^KG("deg", source))
		Set tmp = $Increment(^KG("tagg", bucket, source, predicate, "count"))
		Set tmp = $Increment(^KG("tagg", bucket, source, predicate, "sum"), weight)
		// WARNING: min/max not atomic — see InsertEdge comment. Same race condition applies.
		If '$Data(^KG("tagg", bucket, source, predicate, "min")) || (weight < ^KG("tagg", bucket, source, predicate, "min")) { Set ^KG("tagg", bucket, source, predicate, "min") = weight }
		If '$Data(^KG("tagg", bucket, source, predicate, "max")) || (weight > ^KG("tagg", bucket, source, predicate, "max")) { Set ^KG("tagg", bucket, source, predicate, "max") = weight }
		Do ..UpdateHLL(bucket, source, predicate, target)
		Set attrs = item.%Get("attrs")
		If attrs '= "" && ($IsObject(attrs)) {
			Set iter = attrs.%GetIterator()
			While iter.%GetNext(.key, .val) {
				Set ^KG("edgeprop", ts, source, predicate, target, key) = val
			}
		}
		Set count = count + 1
	}
	Return count
}

ClassMethod UpdateHLL(bucket As %Integer, source As %String, predicate As %String, value As %String) [ Private ]
{
	// 16-register HLL sketch. SHA1 first byte → register index, second byte → leading-zero count.
	// Merge rule: keep max leading-zero count per register (standard HLL update).
	Set hashBytes = $SYSTEM.Encryption.SHA1Hash(value)
	Set regIdx = ($ASCII(hashBytes, 1) # 16) + 1
	Set b1 = $ASCII(hashBytes, 2)
	Set lz = 1
	If b1 = 0 {
		Set lz = 9
	} Else {
		While (b1 # 2) = 0 { Set lz = lz + 1, b1 = b1 \ 2 }
	}
	Set hll = $Get(^KG("tagg", bucket, source, predicate, "hll"), $ListBuild(0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0))
	If lz > $List(hll, regIdx) {
		Set $List(hll, regIdx) = lz
		Set ^KG("tagg", bucket, source, predicate, "hll") = hll
	}
}

ClassMethod GetDistinctCount(source As %String, predicate As %String, tsStart As %Integer, tsEnd As %Integer) As %Integer
{
	Set tsStart = +tsStart
	Set tsEnd = +tsEnd
	Set tBucketDiv = $Select(..#TSUNIT="ms": ..#BUCKETMS, 1: ..#BUCKET)
	Set startBucket = tsStart \ tBucketDiv
	Set endBucket = tsEnd \ tBucketDiv
	// Merge registers across buckets: element-wise max of 16 registers.
	Set merged = $ListBuild(0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0)
	For b = startBucket:1:endBucket {
		Set hll = $Get(^KG("tagg", b, source, predicate, "hll"))
		If hll = "" Continue
		For i = 1:1:16 {
			If $List(hll, i) > $List(merged, i) {
				Set $List(merged, i) = $List(hll, i)
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

ClassMethod QueryWindow(source As %String, predicate As %String, tsStart As %Integer, tsEnd As %Integer) As %String
{
	Set tsStart = +tsStart
	Set tsEnd = +tsEnd
	Set result = "["
	Set first = 1
	Set ts = tsStart - 1
	While 1 {
		Set ts = $Order(^KG("tout", ts))
		If ts = "" Quit
		If ts > tsEnd Quit
		Set s = ""
		While 1 {
			Set s = $Order(^KG("tout", ts, s))
			If s = "" Quit
			If source '= "" { If s '= source { Continue } }
			Set p = ""
			While 1 {
				Set p = $Order(^KG("tout", ts, s, p))
				If p = "" Quit
				If predicate '= "" { If p '= predicate { Continue } }
				Set o = ""
				While 1 {
					Set o = $Order(^KG("tout", ts, s, p, o), 1, w)
					If o = "" Quit
					If 'first Set result = result _ ","
					Set first = 0
					Set result = result _ "{""s"":""" _ s _ """,""p"":""" _ p _ """,""o"":""" _ o _ """,""ts"":" _ ts _ ",""w"":" _ w _ "}"
				}
			}
		}
	}
	Return result _ "]"
}

ClassMethod QueryWindowSources(predicate As %String, tsStart As %Integer, tsEnd As %Integer) As %String
{
	Set tsStart = +tsStart
	Set tsEnd = +tsEnd
	Kill dedup
	Set ts = tsStart - 1
	While 1 {
		Set ts = $Order(^KG("tout", ts))
		If ts = "" Quit
		If ts > tsEnd Quit
		Set s = ""
		While 1 {
			Set s = $Order(^KG("tout", ts, s))
			If s = "" Quit
			If predicate = "" {
				Set dedup(s) = 1
			} Else {
				If $Data(^KG("tout", ts, s, predicate)) Set dedup(s) = 1
			}
		}
	}
	Set result = "[", first = 1, src = ""
	For {
		Set src = $Order(dedup(src))
		Quit:(src = "")
		If 'first { Set result = result _ "," }
		Set first = 0
		Set result = result _ """" _ src _ """"
	}
	Return result _ "]"
}

ClassMethod GetVelocity(nodeId As %String, windowSec As %Integer = 300) As %Integer
{
	Set now = $ZTimeStamp
	Set now = ##class(%SYSTEM.SQL).DATEDIFF("ss", "1970-01-01 00:00:00", $ZDateTime(now, 3, 1))
	Set tBucketDiv = $Select(..#TSUNIT="ms": ..#BUCKETMS, 1: ..#BUCKET)
	Set startBucket = (now - windowSec) \ tBucketDiv
	Set endBucket = now \ tBucketDiv
	Set total = 0
	For b = startBucket:1:endBucket {
		Set total = total + $Get(^KG("bucket", b, nodeId), 0)
	}
	Return total
}

ClassMethod FindBursts(predicate As %String, windowSec As %Integer, threshold As %Integer) As %String
{
	Set now = ##class(%SYSTEM.SQL).DATEDIFF("ss", "1970-01-01 00:00:00", $ZDateTime($ZTimeStamp, 3, 1))
	Set tBucketDiv = $Select(..#TSUNIT="ms": ..#BUCKETMS, 1: ..#BUCKET)
	Set startBucket = (now - windowSec) \ tBucketDiv
	Set endBucket = now \ tBucketDiv
	Set result = "["
	Set first = 1
	Set b = startBucket - 1
	For {
		Set b = $Order(^KG("bucket", b))
		Quit:b=""
		If b > endBucket Quit
		Set node = ""
		For {
			Set node = $Order(^KG("bucket", b, node), 1, cnt)
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
			Set result = result _ "{""id"":""" _ node _ """,""velocity"":" _ total _ "}"
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

ClassMethod PurgeBefore(tsEnd As %Integer)
{
	Set tsEnd = +tsEnd
	Set tBucketDiv = $Select(..#TSUNIT="ms": ..#BUCKETMS, 1: ..#BUCKET)
	// Only kill aggregate buckets strictly before the bucket containing tsEnd.
	Set maxSafeBucket = (tsEnd \ tBucketDiv) - 1
	Set ts = ""
	For {
		Set ts = $Order(^KG("tout", ts))
		Quit:(ts = "")!(ts >= tsEnd)
		Set s = ""
		For {
			Set s = $Order(^KG("tout", ts, s))
			Quit:s=""
			Set p = ""
			For {
				Set p = $Order(^KG("tout", ts, s, p))
				Quit:p=""
				Set o = ""
				For {
					Set o = $Order(^KG("tout", ts, s, p, o))
					Quit:o=""
					Kill ^KG("tout", ts, s, p, o)
					Kill ^KG("tin",  ts, o, p, s)
					Kill ^KG("edgeprop", ts, s, p, o)
				}
			}
		}
		// Only kill aggregate buckets that are fully expired.
		Set bucket = ts \ tBucketDiv
		If bucket <= maxSafeBucket {
			Kill ^KG("tagg", bucket)
			Kill ^KG("bucket", bucket)
		}
		Kill ^KG("tout", ts)
	}
}

/// Delete raw temporal edges with ts < tsEnd, preserving aggregates.
/// Returns the number of edges deleted. Strict < boundary.
ClassMethod PurgeRawBefore(tsEnd As %Integer) As %Integer
{
	Set tsEnd = +tsEnd
	Set count = 0
	Set ts = ""
	For {
		Set ts = $Order(^KG("tout", ts))
		Quit:(ts = "")!(ts >= tsEnd)
		Set s = ""
		For {
			Set s = $Order(^KG("tout", ts, s))
			Quit:s=""
			Set p = ""
			For {
				Set p = $Order(^KG("tout", ts, s, p))
				Quit:p=""
				Set o = ""
				For {
					Set o = $Order(^KG("tout", ts, s, p, o))
					Quit:o=""
					Kill ^KG("tout", ts, s, p, o)
					Kill ^KG("tin",  ts, o, p, s)
					Kill ^KG("edgeprop", ts, s, p, o)
					Set count = count + 1
				}
			}
		}
		Kill ^KG("tout", ts)
	}
	Return count
}

/// Intern an attribute JSON object: canonicalize, hash, store once, return SHA1 hex.
/// Returns "" on invalid JSON input. Idempotent.
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
	// SHA1 hash → lowercase hex
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

ClassMethod QueryWindowInbound(target As %String, predicate As %String, tsStart As %Integer, tsEnd As %Integer) As %String
{
	Set tsStart = +tsStart
	Set tsEnd = +tsEnd
	Set result = "["
	Set first = 1
	Set ts = tsStart - 1
	While 1 {
		Set ts = $Order(^KG("tin", ts))
		If ts = "" Quit
		If ts > tsEnd Quit
		Set o = ""
		While 1 {
			Set o = $Order(^KG("tin", ts, o))
			If o = "" Quit
			If target '= "" { If o '= target { Continue } }
			Set p = ""
			While 1 {
				Set p = $Order(^KG("tin", ts, o, p))
				If p = "" Quit
				If predicate '= "" { If p '= predicate { Continue } }
				Set s = ""
				While 1 {
					Set s = $Order(^KG("tin", ts, o, p, s), 1, w)
					If s = "" Quit
					If 'first Set result = result _ ","
					Set first = 0
					Set result = result _ "{""s"":""" _ s _ """,""p"":""" _ p _ """,""o"":""" _ o _ """,""ts"":" _ ts _ ",""w"":" _ w _ "}"
				}
			}
		}
	}
	Return result _ "]"
}

ClassMethod GetAggregate(source As %String, predicate As %String, metric As %String, tsStart As %Integer, tsEnd As %Integer) As %String
{
	Set tsStart = +tsStart
	Set tsEnd = +tsEnd
	Set tBucketDiv = $Select(..#TSUNIT="ms": ..#BUCKETMS, 1: ..#BUCKET)
	Set startBucket = tsStart \ tBucketDiv
	Set endBucket = tsEnd \ tBucketDiv
	Set totalCount = 0, totalSum = 0
	Set globalMin = "", globalMax = ""
	For b = startBucket:1:endBucket {
		Set cnt = +$Get(^KG("tagg", b, source, predicate, "count"), 0)
		Set sm = +$Get(^KG("tagg", b, source, predicate, "sum"), 0)
		Set mn = $Get(^KG("tagg", b, source, predicate, "min"))
		Set mx = $Get(^KG("tagg", b, source, predicate, "max"))
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

ClassMethod GetBucketGroups(predicate As %String, tsStart As %Integer, tsEnd As %Integer, sourcePrefix As %String = "") As %String
{
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
			Set src = $Order(^KG("tagg", b, src))
			Quit:(src = "")
			If prefixLen > 0 && ($Extract(src, 1, prefixLen) '= sourcePrefix) { Continue }
			Set p = ""
			For {
				Set p = $Order(^KG("tagg", b, src, p))
				Quit:(p = "")
				If predicate '= "" && (p '= predicate) { Continue }
				Set cnt = +$Get(^KG("tagg", b, src, p, "count"), 0)
				Set sm = +$Get(^KG("tagg", b, src, p, "sum"), 0)
				Set mn = $Get(^KG("tagg", b, src, p, "min"))
				Set mx = $Get(^KG("tagg", b, src, p, "max"))
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
			Set result = result _ "{""source"":""" _ src _ """,""predicate"":""" _ p _ """,""count"":" _ cnt _ ",""sum"":" _ sm _ ",""avg"":" _ avg _ ",""min"":" _ mn _ ",""max"":" _ mx _ "}"
		}
	}
	Return result _ "]"
}

ClassMethod GetBucketGroupTargets(source As %String, predicate As %String, tsStart As %Integer, tsEnd As %Integer) As %String
{
	Set tsStart = +tsStart
	Set tsEnd = +tsEnd
	Set ts = tsStart - 1
	Kill dedup
	For {
		Set ts = $Order(^KG("tout", ts))
		Quit:(ts = "")
		If ts > tsEnd Quit
		If $Data(^KG("tout", ts, source)) {
			Set p = ""
			For {
				Set p = $Order(^KG("tout", ts, source, p))
				Quit:(p = "")
				If predicate '= "" && (p '= predicate) { Continue }
				Set o = ""
				For {
					Set o = $Order(^KG("tout", ts, source, p, o))
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
		Set result = result _ """" _ tgt _ """"
	}
	Return result _ "]"
}

ClassMethod GetEdgeAttrs(ts As %Integer, source As %String, predicate As %String, target As %String) As %String
{
	Set ts = +ts
	Set result = ##class(%DynamicObject).%New()
	Set key = ""
	While 1 {
		Set key = $Order(^KG("edgeprop", ts, source, predicate, target, key), 1, val)
		If key = "" Quit
		Do result.%Set(key, val)
	}
	Return result.%ToJSON()
}

}
