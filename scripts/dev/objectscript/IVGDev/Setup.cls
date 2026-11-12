/// Test-only: IVG's dev tooling (scripts/dev/tcp_setup.py) makes and drops scratch namespaces through these methods,
/// over TCP and the Native API, with no `iris session`. Loaded into %SYS by tcp_setup.py; never shipped (it is not in
/// iris_src/src, and the wheel packages only that).
/// Config.Databases.Create and Config.Namespaces.Create take their properties as a local array by reference, which the
/// Native API cannot pass, so every method here takes plain arguments. Each answers "" when it did what it says, else
/// "FAIL <step>: <error text>"; none throws for a failed step.
/// Deleting is refused for any name that does not start with IVG: callers check their own, narrower, prefixes first.
Class IVGDev.Setup [ Abstract ]
{

/// Replaced by the source's hash when tcp_setup.py loads the class; it reloads the class when the hash differs.
Parameter SOURCEHASH = "";

ClassMethod SourceHash() As %String
{
    Return ..#SOURCEHASH
}

ClassMethod NamespaceExists(pName As %String) As %Boolean
{
    Return ##class(Config.Namespaces).Exists(pName)
}

/// "globals,routines": the databases namespace pName maps; "" when it does not exist.
ClassMethod NamespaceDatabases(pName As %String) As %String
{
    If '##class(Config.Namespaces).Exists(pName) {
        Return ""
    }
    Set tSC = ##class(Config.Namespaces).Get(pName, .tProps)
    If $$$ISERR(tSC) {
        Return "FAIL ns: "_$System.Status.GetErrorText(tSC)
    }
    Return $Get(tProps("Globals"))_","_$Get(tProps("Routines"))
}

/// Resource pName, unless it exists.
ClassMethod CreateResource(pName As %String, pDescription As %String = "") As %String
{
    If ##class(Security.Resources).Exists(pName) {
        Return ""
    }
    Set tSC = ##class(Security.Resources).Create(pName, pDescription, "", "")
    If $$$ISERR(tSC) {
        Return "FAIL res: "_$System.Status.GetErrorText(tSC)
    }
    Return ""
}

/// Directory pDirectory, a database in it (on resource pResource when given, else IRIS's %DB_%DEFAULT) and its
/// configuration under pName; each step skipped when what it makes exists.
ClassMethod CreateDatabase(pName As %String, pDirectory As %String, pResource As %String = "") As %String
{
    Set tDir = ##class(%File).NormalizeDirectory(pDirectory)
    Do ##class(%File).CreateDirectoryChain(tDir)
    If '##class(%File).DirectoryExists(tDir) {
        Return "FAIL dir: could not create "_tDir
    }
    If '##class(SYS.Database).%ExistsId(tDir) {
        Set tDB = ##class(SYS.Database).%New()
        Set tDB.Directory = tDir
        If pResource '= "" {
            Set tDB.ResourceName = pResource
        }
        Set tSC = tDB.%Save()
        If $$$ISERR(tSC) {
            Return "FAIL db: "_$System.Status.GetErrorText(tSC)
        }
    }
    If '##class(Config.Databases).Exists(pName) {
        Set tProps("Directory") = tDir
        Set tSC = ##class(Config.Databases).Create(pName, .tProps)
        If $$$ISERR(tSC) {
            Return "FAIL cfgdb: "_$System.Status.GetErrorText(tSC)
        }
    }
    Return ""
}

/// Namespace pName with its globals in database pGlobals and its routines in pRoutines.
ClassMethod CreateNamespace(pName As %String, pGlobals As %String, pRoutines As %String) As %String
{
    If ##class(Config.Namespaces).Exists(pName) {
        Return "FAIL ns: "_pName_" exists"
    }
    Set tProps("Globals") = pGlobals
    Set tProps("Routines") = pRoutines
    Set tSC = ##class(Config.Namespaces).Create(pName, .tProps)
    If $$$ISERR(tSC) {
        Return "FAIL ns: "_$System.Status.GetErrorText(tSC)
    }
    Return ""
}

/// The IDs of the Task Manager tasks that run in namespace pName, comma separated: a task (IVG's EmbedQueue worker)
/// outlives its namespace and keeps running against it.
ClassMethod TaskIds(pName As %String) As %String
{
    Set tIds = ""
    Set tRS = ##class(%SQL.Statement).%ExecDirect(, "SELECT ID FROM %SYS.Task WHERE NameSpace = ?", pName)
    If tRS.%SQLCODE < 0 {
        Return "FAIL task: SQLCODE "_tRS.%SQLCODE_" "_tRS.%Message
    }
    While tRS.%Next() {
        Set tIds = tIds_$Select(tIds = "": "", 1: ",")_tRS.%Get("ID")
    }
    Return tIds
}

ClassMethod DeleteTask(pId As %String) As %String
{
    Set tSC = ##class(%SYS.Task).%DeleteId(pId)
    If $$$ISERR(tSC) {
        Return "FAIL task: "_$System.Status.GetErrorText(tSC)
    }
    Return ""
}

/// The configuration of namespace pName; its databases are left.
ClassMethod DeleteNamespace(pName As %String) As %String
{
    If $Extract(pName, 1, 3) '= "IVG" {
        Return "FAIL refused: "_pName_" does not start with IVG"
    }
    If '##class(Config.Namespaces).Exists(pName) {
        Return ""
    }
    Set tSC = ##class(Config.Namespaces).Delete(pName)
    If $$$ISERR(tSC) {
        Return "FAIL ns: "_$System.Status.GetErrorText(tSC)
    }
    Return ""
}

/// Database pName: its configuration, the database, and its directory (DeleteDatabase leaves the directory and its
/// empty stream folder behind). With pDeleteResource, resource %DB_<pName> too.
ClassMethod DeleteDatabase(pName As %String, pDeleteResource As %Boolean = 0) As %String
{
    If $Extract(pName, 1, 3) '= "IVG" {
        Return "FAIL refused: "_pName_" does not start with IVG"
    }
    If '##class(Config.Databases).Exists(pName) {
        Return ""
    }
    Set tSC = ##class(Config.Databases).Get(pName, .tProps)
    If $$$ISERR(tSC) {
        Return "FAIL cfgdb: "_$System.Status.GetErrorText(tSC)
    }
    Set tDir = $Get(tProps("Directory"))
    Set tSC = ##class(Config.Databases).Delete(pName)
    If $$$ISERR(tSC) {
        Return "FAIL cfgdb: "_$System.Status.GetErrorText(tSC)
    }
    Set tSC = ##class(SYS.Database).DeleteDatabase(tDir)
    If $$$ISERR(tSC) {
        Return "FAIL db: "_$System.Status.GetErrorText(tSC)
    }
    Do ##class(%File).RemoveDirectoryTree(tDir)
    If ##class(%File).DirectoryExists(tDir) {
        Return "FAIL dir: "_tDir_" is still there"
    }
    If pDeleteResource, ##class(Security.Resources).Exists("%DB_"_pName) {
        Set tSC = ##class(Security.Resources).Delete("%DB_"_pName)
        If $$$ISERR(tSC) {
            Return "FAIL res: "_$System.Status.GetErrorText(tSC)
        }
    }
    Return ""
}

/// Security.Users.UnExpireUserPasswords for the users pPattern names ("*": every user).
ClassMethod UnExpireUserPasswords(pPattern As %String = "*") As %String
{
    Set tSC = ##class(Security.Users).UnExpireUserPasswords(pPattern)
    If $$$ISERR(tSC) {
        Return "FAIL users: "_$System.Status.GetErrorText(tSC)
    }
    Return ""
}

}
