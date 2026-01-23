Class Graph.KG.Traversal Extends %RegisteredObject
{

ClassMethod BuildKG() As %Status
{
	Try {
		Kill ^KG
		&sql(DECLARE c1 CURSOR FOR SELECT s,label FROM rdf_labels)
		&sql(OPEN c1)
		For {
			&sql(FETCH c1 INTO :s, :label)
			Quit:$SQLCODE'=0
			Set ^KG("label",label,s)=""
		}
		&sql(CLOSE c1)

		&sql(DECLARE c2 CURSOR FOR SELECT s,key,val FROM rdf_props)
		&sql(OPEN c2)
		For {
			&sql(FETCH c2 INTO :s, :k, :v)
			Quit:$SQLCODE'=0
			Set ^KG("prop",k,v,s)="", ^KG("prop:",k,s)=""
		}
		&sql(CLOSE c2)

		&sql(DECLARE c3 CURSOR FOR SELECT s,p,o_id FROM rdf_edges)
		&sql(OPEN c3)
		For {
			&sql(FETCH c3 INTO :s, :p, :o)
			Quit:$SQLCODE'=0
			Set ^KG("out",s,p,o)="", ^KG("in",o,p,s)=""
			Set ^KG("deg",s)=$Get(^KG("deg",s))+1
			Set ^KG("degp",s,p)=$Get(^KG("degp",s,p))+1
		}
		&sql(CLOSE c3)
		Return $$$OK
	} Catch ex {
		Return ex.AsStatus()
	}
}

ClassMethod BFS_JSON(srcId As %String, preds As %DynamicArray, maxHops As %Integer = 2, dstLabel As %String = "") As %DynamicArray [ Language = python ]
{
    import iris, json
    g = iris.gref("^KG")
    src = str(srcId)
    want_label = str(dstLabel) if dstLabel else ""
    pred_seq = []
    if preds is not None:
        for i in range(preds._Size()):
            pred_seq.append(str(preds._Get(i)))

    seen = set([src])
    frontier = [src]
    path_id = 0
    out = iris.cls('%DynamicArray')._New()

    for hop in range(1, int(maxHops)+1):
        if not frontier: break
        next_front = set()
        for s in frontier:
            if hop <= len(pred_seq) and pred_seq[hop-1]:
                p = pred_seq[hop-1]
                o = ""
                while True:
                    o = g.next("out", s, p, o)
                    if o == "": break
                    if want_label and g.get("label", want_label, o) is None: 
                        continue
                    path_id += 1
                    stepObj = {"id": path_id, "step": hop, "s": s, "p": p, "o": o}
                    out._Push(iris.cls('%DynamicObject')._FromJSON(json.dumps(stepObj)))
                    if o not in seen: next_front.add(o)
            else:
                p = ""
                while True:
                    p = g.next("out", s, p)
                    if p == "": break
                    o = ""
                    while True:
                        o = g.next("out", s, p, o)
                        if o == "": break
                        if want_label and g.get("label", want_label, o) is None:
                            continue
                        path_id += 1
                        stepObj = {"id": path_id, "step": hop, "s": s, "p": p, "o": o}
                        out._Push(iris.cls('%DynamicObject')._FromJSON(json.dumps(stepObj)))
                        if o not in seen: next_front.add(o)
        seen |= next_front
        frontier = list(next_front)

    return out
}

}
