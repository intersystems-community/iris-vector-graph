Class Graph.Index.KG_Out.idx Extends %Library.FunctionalIndex
{

Parameter SUPPORTSSHARDING = 1;

Method InsertIndex(rowId As %Integer) As %Status
{
  Quit $$$OK
}

Method UpdateIndex(rowId As %Integer) As %Status
{
  Quit $$$OK
}

Method DeleteIndex(rowId As %Integer) As %Status
{
  Quit $$$OK
}

ClassMethod Find(pSearch As %Binary) As %SQLQuery [ SqlProc ]
{
  &sql(DECLARE q CURSOR FOR SELECT 1 WHERE 1=0)
  &sql(OPEN q)
  Quit q
}

}
