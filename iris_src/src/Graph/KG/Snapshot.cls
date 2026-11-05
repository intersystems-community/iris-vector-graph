Class Graph.KG.Snapshot Extends %RegisteredObject
{

ClassMethod ExportGlobals(outputDir As %String, globalListJson As %String) As %String
{
	Do ##class(%Library.File).CreateDirectoryChain(outputDir)
	Set globalList = ##class(%DynamicArray).%FromJSON(globalListJson)
	Set exportedFiles = ##class(%DynamicArray).%New()
	Set gCount = globalList.%Size()
	For i = 0:1:(gCount-1) {
		Set entry = globalList.%Get(i)
		Set globalName = entry.%Get("name")
		If globalName = "" Continue
		Set exportFile = outputDir _ globalName _ ".gof"
		// %GlobalEdit has no Export method; a global is exported as a .GBL item.
		Set sc = $System.OBJ.Export(globalName _ ".GBL", exportFile, "/nodisplay")
		If $$$ISOK(sc) {
			Do exportedFiles.%Push(exportFile)
		}
	}
	Quit exportedFiles.%ToJSON()
}

ClassMethod ImportGlobals(inputDir As %String) As %String
{
	Set imported = 0
	Set rs = ##class(%ResultSet).%New("%Library.File:FileSet")
	Do rs.Execute(inputDir, "*.gof")
	While rs.Next() {
		Set fileName = rs.Get("Name")
		Set sc = $System.OBJ.Load(fileName, "/nodisplay")
		If $$$ISOK(sc) Set imported = imported + 1
	}
	Quit "{""imported"":"_imported_"}"
}

ClassMethod ReadFileChunk(filePath As %String, offset As %Integer = 0, chunkSize As %Integer = 1048576) As %String
{
	Set stream = ##class(%Stream.FileCharacter).%New()
	Do stream.LinkToFile(filePath)
	Do stream.Rewind()
	If offset > 0 {
		Do stream.Read(offset)
	}
	Set chunk = stream.Read(chunkSize)
	Quit chunk
}

ClassMethod WriteFileChunk(filePath As %String, content As %String, append As %Integer = 0)
{
	Set stream = ##class(%Stream.FileCharacter).%New()
	Do stream.LinkToFile(filePath)
	If 'append Do stream.Rewind()
	If append Do stream.MoveToEnd()
	Do stream.Write(content)
	Do stream.%Save()
}

ClassMethod GetFileSize(filePath As %String) As %Integer
{
	Quit ##class(%Library.File).GetFileSize(filePath)
}

ClassMethod DeleteDir(dirPath As %String) As %Integer
{
	Set sc = ##class(%Library.File).RemoveDirectoryTree(dirPath)
	Quit +sc
}

ClassMethod FileExists(filePath As %String) As %Integer
{
	Quit ##class(%Library.File).Exists(filePath)
}

/// The value at pGlobal(the subscripts in the JSON array pSubsJSON) as JSON, for save_snapshot; "" when the node has
/// no value. A $vector is {"vec": its type, "v": [its elements]}, a $List whose elements are all $vectors (PLAID's
/// docPacked) {"list": [those]}, anything else {"v": the value as a string}. The Native API reads neither of the
/// first two: its getters raise <LIST ERROR> (type 16) on a $vector (4.6.0 R302).
ClassMethod NodeValueJSON(pGlobal As %String, pSubsJSON As %String) As %String
{
	Set tRef = ..NodeRef(pGlobal, pSubsJSON)
	Quit:($Data(@tRef)#10)=0 ""
	Quit ..Encode(@tRef).%ToJSON()
}

/// Write back a value NodeValueJSON read: pValueJSON is one of its three forms.
ClassMethod SetNodeJSON(pGlobal As %String, pSubsJSON As %String, pValueJSON As %String)
{
	Set tRef = ..NodeRef(pGlobal, pSubsJSON)
	Set @tRef = ..Decode(##class(%DynamicObject).%FromJSON(pValueJSON))
}

/// The global reference $Name gives for pGlobal and the subscripts; pGlobal must be a global name.
ClassMethod NodeRef(pGlobal As %String, pSubsJSON As %String) As %String [ Private ]
{
	If pGlobal'?1"^"1(1A,1"%").(1AN,1".") {
		$$$ThrowStatus($$$ERROR($$$GeneralError, "not a global name: "_pGlobal))
	}
	Set tRef = pGlobal
	Set tIter = ##class(%DynamicArray).%FromJSON(pSubsJSON).%GetIterator()
	While tIter.%GetNext(.tKey, .tSub) {
		Set tRef = $Name(@tRef@(tSub))
	}
	Quit tRef
}

ClassMethod IsVector(pVal) As %Boolean [ Private ]
{
	// $IsVector("") is 1
	Quit (pVal'="") && $IsVector(pVal)
}

ClassMethod Encode(pVal) As %DynamicObject [ Private ]
{
	If ..IsVector(pVal) {
		Set tElems = []
		For tI = 1:1:$VectorOp("length", pVal) {
			If $VectorDefined(pVal, tI) {
				Do tElems.%Push($Vector(pVal, tI))
			} Else {
				Do tElems.%Push("", "null")
			}
		}
		Quit {"vec": ($VectorOp("type", pVal)), "v": (tElems)}
	}
	If (pVal'="") && $ListValid(pVal) {
		Set tElems = []
		Set tPtr = 0
		Set tAllVectors = 1
		While $ListNext(pVal, tPtr, tElem) {
			If '$Data(tElem) || '..IsVector(tElem) {
				Set tAllVectors = 0
				Quit
			}
			Do tElems.%Push(..Encode(tElem))
		}
		Quit:tAllVectors {"list": (tElems)}
	}
	Quit {"v": (pVal_"")}
}

ClassMethod Decode(pObj As %DynamicObject) [ Private ]
{
	If pObj.%IsDefined("vec") {
		Set tType = pObj.vec
		Set tVec = ""
		Set tIter = pObj.v.%GetIterator()
		While tIter.%GetNext(.tKey, .tElem) {
			Continue:pObj.v.%GetTypeOf(tKey)="null"
			Set $Vector(tVec, tKey + 1, tType) = tElem
		}
		Quit tVec
	}
	If pObj.%IsDefined("list") {
		Set tList = ""
		Set tIter = pObj.list.%GetIterator()
		While tIter.%GetNext(.tKey, .tElem) {
			Set tList = tList_$ListBuild(..Decode(tElem))
		}
		Quit tList
	}
	Quit pObj.v
}

}
