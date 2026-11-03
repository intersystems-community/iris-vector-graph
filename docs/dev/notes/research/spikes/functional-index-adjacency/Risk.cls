Class IVGSpike.Risk
{

/// Insert one edge in a transaction and roll it back; report what the derived stores keep.
ClassMethod Ghost(variant As %String) As %String
{
	Set root = $Select(variant = "L": "^IVGSpikeL", 1: "^IVGSpike")
	Set vBefore = +$Get(@root@("__version"))
	TSTART
	If variant = "L" {
		&sql(INSERT INTO IVGSpike.EdgeL (GraphId, S, P, O) VALUES ('', 'ghost1', 'R', 'ghost2'))
	} Else {
		&sql(INSERT INTO IVGSpike.Edge (GraphId, S, P, O) VALUES ('', 'ghost1', 'R', 'ghost2'))
	}
	TROLLBACK
	Quit "out=" _ $Data(@root@("out", 0, "ghost1", "R", "ghost2")) _ " in=" _ $Data(@root@("in", 0, "ghost2", "R", "ghost1")) _ " deg=" _ $Get(@root@("deg", 0, "ghost1"), "<none>") _ " degp=" _ $Get(@root@("degp", 0, "ghost1", "R"), "<none>") _ " version moved by " _ (+$Get(@root@("__version")) - vBefore)
}

/// A hub: every writer's edges share the source "hub"; each writer uses its own targets.
ClassMethod Hub(variant As %String, jobs As %Integer, n As %Integer) As %String
{
	Kill ^IVGSpikeDone, ^IVGSpikeErr
	Set t0 = $ZHorolog
	For j = 1:1:jobs {
		Job ##class(IVGSpike.Risk).HubWorker(variant, n, j)
	}
	For wait = 1:1:1200 {
		Quit:(+$Get(^IVGSpikeDone) '< jobs)
		Hang 0.1
	}
	Set wall = $ZHorolog - t0
	Set root = $Select(variant = "L": "^IVGSpikeL", 1: "^IVGSpike")
	Quit $FNumber(wall, "", 2) _ "s wall; writers finished=" _ +$Get(^IVGSpikeDone) _ " of " _ jobs _ "; deg(hub)=" _ +$Get(@root@("deg", 0, "hub")) _ " expected " _ (jobs * n) _ "; errors=" _ +$Get(^IVGSpikeErr) _ " " _ $Get(^IVGSpikeErr, 0)
}

ClassMethod HubWorker(variant As %String, n As %Integer, j As %Integer)
{
	Set i = 0
	While i < n {
		TSTART
		For b = 1:1:500 {
			Quit:i>=n
			Set o = "t" _ j _ "_" _ i, i = i + 1
			If variant = "L" {
				&sql(INSERT INTO IVGSpike.EdgeL (GraphId, S, P, O) VALUES ('', 'hub', 'R', :o))
			} Else {
				&sql(INSERT INTO IVGSpike.Edge (GraphId, S, P, O) VALUES ('', 'hub', 'R', :o))
			}
			If SQLCODE '= 0 { Set ^IVGSpikeErr = $Get(^IVGSpikeErr) + 1, ^IVGSpikeErr(1) = SQLCODE _ " " _ $Get(%msg) }
		}
		TCOMMIT
	}
	Set tmp = $Increment(^IVGSpikeDone)
}

/// Overlapping sources, random order, 50-row transactions: the lock-ordering hazard of the lock+Set variant.
ClassMethod Chaos(variant As %String, jobs As %Integer, n As %Integer, pool As %Integer = 40) As %String
{
	Kill ^IVGSpikeDone, ^IVGSpikeErr
	Set t0 = $ZHorolog
	For j = 1:1:jobs {
		Job ##class(IVGSpike.Risk).ChaosWorker(variant, n, j, pool)
	}
	For wait = 1:1:1800 {
		Quit:(+$Get(^IVGSpikeDone) '< jobs)
		Hang 0.1
	}
	Set wall = $ZHorolog - t0
	Set root = $Select(variant = "L": "^IVGSpikeL", 1: "^IVGSpike")
	Set sum = 0, s = "" For { Set s = $Order(@root@("deg", 0, s)) Quit:s=""  Set sum = sum + @root@("deg", 0, s) }
	If variant = "L" { &sql(SELECT COUNT(*) INTO :rows FROM IVGSpike.EdgeL) } Else { &sql(SELECT COUNT(*) INTO :rows FROM IVGSpike.Edge) }
	Quit $FNumber(wall, "", 2) _ "s wall; finished=" _ +$Get(^IVGSpikeDone) _ "/" _ jobs _ "; rows=" _ rows _ " sum(deg)=" _ sum _ "; failed statements=" _ +$Get(^IVGSpikeErr) _ " (" _ $Get(^IVGSpikeErr(1)) _ ")"
}

ClassMethod ChaosWorker(variant As %String, n As %Integer, j As %Integer, pool As %Integer)
{
	Set i = 0
	While i < n {
		TSTART
		For b = 1:1:50 {
			Quit:i>=n
			Set s = "s" _ ($Random(pool)), o = "t" _ j _ "_" _ i, i = i + 1
			If variant = "L" {
				&sql(INSERT INTO IVGSpike.EdgeL (GraphId, S, P, O) VALUES ('', :s, 'R', :o))
			} Else {
				&sql(INSERT INTO IVGSpike.Edge (GraphId, S, P, O) VALUES ('', :s, 'R', :o))
			}
			If SQLCODE '= 0 { Set tmp = $Increment(^IVGSpikeErr), ^IVGSpikeErr(1) = SQLCODE _ " " _ $Extract($Get(%msg), 1, 60) }
		}
		TCOMMIT
	}
	Set tmp = $Increment(^IVGSpikeDone)
}

ClassMethod Reset()
{
	Do ##class(IVGSpike.Edge).%KillExtent()
	Do ##class(IVGSpike.EdgeL).%KillExtent()
	Kill ^IVGSpike, ^IVGSpikeL, ^IVGSpikeDone, ^IVGSpikeErr
}

}
