/// Fast bulk seeder for benchmark graphs.
/// Python writes edge list to ^KGSeed staging global, then calls SeedFromStaging
/// which reads it server-side — avoids per-edge callin round-trips.
Class Graph.KG.BenchSeeder Extends %RegisteredObject
{

ClassMethod SeedFromStaging(version As %String = "1") As %Status
{
	Set count = $Get(^KGSeed("count"), 0)
	Set i = 0
	While i < count {
		Set pair = ^KGSeed(i)
		Set src = $Piece(pair, $Char(9), 1)
		Set dst = $Piece(pair, $Char(9), 2)
		Set ^KG("out", src, "REL", dst) = 1
		Set ^KG("in", dst, "REL", src) = 1
		Set ^KG("deg", src) = $Get(^KG("deg", src), 0) + 1
		Set i = i + 1
	}
	Set ^KG("__version") = version
	Kill ^KGSeed
	Return $$$OK
}

}
