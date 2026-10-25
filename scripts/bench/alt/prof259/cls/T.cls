Class ZIVGP.T Extends %RegisteredObject
{

/// Run ObjectScript pCode in this process; the code sets ^||zout (returned).
ClassMethod X(pCode As %String) As %String
{
	Kill ^||zout
	Xecute pCode
	Quit $Get(^||zout)
}

}
