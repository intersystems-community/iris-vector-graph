/// A test-only embedding class (4.6.0 R116): EMBEDDING(text, 'ivgtest-fake') with no model behind it.
/// Text ending "a" is [1,0,0], "b" [1,1,0], "c" [0,1,0], anything else [0,0,1]. Loaded by the e2e test that needs it,
/// never shipped.
Class IVGTest.FakeEmbedding Extends %Embedding.Interface
{

ClassMethod Embedding(input As %String, configuration As %String) As %Library.Vector
{
    Set tValues = $Case($Extract(input, *), "a": "1,0,0", "b": "1,1,0", "c": "0,1,0", : "0,0,1")
    Set tVec = ""
    For i = 1:1:3 {
        Set $Vector(tVec, i, "double") = +$Piece(tValues, ",", i)
    }
    Quit tVec
}

ClassMethod IsValidConfig(config As %DynamicObject, ByRef errorMsg As %String) As %Boolean
{
    Quit 1
}

}
