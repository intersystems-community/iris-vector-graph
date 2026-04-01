Class Graph.KG.TemporalIndex Extends %RegisteredObject
{

Parameter BUCKET = 300;

ClassMethod InsertEdge(source As %String, predicate As %String, target As %String, timestamp As %Integer = "", weight As %Double = 1.0)
{
	If timestamp = "" Set timestamp = ##class(%SYSTEM.SQL).DATEDIFF("ss", "1970-01-01 00:00:00", $ZDateTime($ZTimeStamp, 3, 1))
	Set ^KG("tout", timestamp, source, predicate, target) = weight
	Set ^KG("tin", timestamp, target, predicate, source) = weight
	Set bucket = timestamp \ ..#BUCKET
	Set tmp = $Increment(^KG("bucket", bucket, source))
	Set ^KG("out", source, predicate, target) = weight
	Set ^KG("in", target, predicate, source) = weight
	Set tmp = $Increment(^KG("deg", source))
}

ClassMethod BulkInsert(batchJSON As %String) As %Integer
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
		Set ^KG("tout", ts, source, predicate, target) = weight
		Set ^KG("tin", ts, target, predicate, source) = weight
		Set bucket = ts \ ..#BUCKET
		Set tmp = $Increment(^KG("bucket", bucket, source))
		Set ^KG("out", source, predicate, target) = weight
		Set ^KG("in", target, predicate, source) = weight
		Set tmp = $Increment(^KG("deg", source))
		Set count = count + 1
	}
	Return count
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

ClassMethod GetVelocity(nodeId As %String, windowSec As %Integer = 300) As %Integer
{
	Set now = $ZTimeStamp
	Set now = ##class(%SYSTEM.SQL).DATEDIFF("ss", "1970-01-01 00:00:00", $ZDateTime(now, 3, 1))
	Set startBucket = (now - windowSec) \ ..#BUCKET
	Set endBucket = now \ ..#BUCKET
	Set total = 0
	For b = startBucket:1:endBucket {
		Set total = total + $Get(^KG("bucket", b, nodeId), 0)
	}
	Return total
}

ClassMethod FindBursts(predicate As %String, windowSec As %Integer, threshold As %Integer) As %String
{
	Set now = ##class(%SYSTEM.SQL).DATEDIFF("ss", "1970-01-01 00:00:00", $ZDateTime($ZTimeStamp, 3, 1))
	Set startBucket = (now - windowSec) \ ..#BUCKET
	Set endBucket = now \ ..#BUCKET
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
}

}
