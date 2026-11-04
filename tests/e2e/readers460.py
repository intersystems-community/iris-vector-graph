"""Users that hold the `/kg` role's grants, or all of them but some, for the 4.6.0 privilege files (R143, R146).

Each reader is a role `<NS>_<TAG>_ROLE` and a user `<NS>_<TAG>_USER` (IRIS refuses a user and a role of one name),
both instance-wide in %SYS and so named after the scratch namespace (`ns460alt.NS`). The role holds read on the
namespace database's resource and, in the IVG Schema, the grants `Graph.KG.Service.GrantWebAppSql` makes, less the
tables in `omit`. `drop` revokes the role's SQL privileges and deletes both.

`call` logs in as the reader and runs ObjectScript on one terminal line ending in `halt`: the terminal prompt needs
%Development, which a reader does not hold.
"""

from __future__ import annotations

from tests.e2e import ns460alt as alt

PASSWORD = "Reader460fx"
#: kg_VEC_ROUTE_GUARD: kg_KNN_VEC calls it with the caller's privileges (4.6.0 R265)
PROCEDURES = ("kg_KNN_VEC", "kg_RRF_FUSE", "kg_TXT", "kg_VEC_ROUTE_GUARD")
TABLES = ("kg_NodeEmbeddings", "rdf_labels", "docs", "embedding_registry")


def names(tag: str) -> tuple:
    return f"{alt.NS}_{tag}_ROLE", f"{alt.NS}_{tag}_USER"


def _database_resource() -> str:
    out = alt.session([f'set db=##class(SYS.Database).%OpenId("/usr/irissys/mgr/{alt.NS.lower()}/")',
                       'write "<<DBRES:",db.ResourceName,":DBRES>>",!'], namespace="%SYS")
    return alt.marked(out, "DBRES")


def make(tag: str, omit: tuple = ()) -> str:
    """Create the reader `tag` (dropping one left over first); returns its user name."""
    drop(tag)
    role, user = names(tag)
    out = alt.session([
        f'set sc=##class(Security.Roles).Create("{role}","4.6.0 privilege test reader","{_database_resource()}:R")',
        'write "<<ROLE:",$system.Status.GetErrorText(sc),":ROLE>>",!',
        f'set sc=##class(Security.Users).Create("{user}","{role}","{PASSWORD}","4.6.0 privilege test","{alt.NS}")',
        'write "<<USER:",$system.Status.GetErrorText(sc),":USER>>",!'], namespace="%SYS")
    assert alt.marked(out, "ROLE") == "" and alt.marked(out, "USER") == "", out
    grants = [f"GRANT EXECUTE ON {alt.SCHEMA}.{p} TO {role}" for p in PROCEDURES]
    grants += [f"GRANT SELECT ON {alt.SCHEMA}.{t} TO {role}" for t in TABLES if t not in omit]
    out = alt.session([f'set rs=##class(%SQL.Statement).%ExecDirect(,"{g}") write "<<G:",rs.%SQLCODE,":G>>",!'
                       for g in grants])
    assert out.count("<<G:0:G>>") == len(grants), out
    return user


def drop(tag: str) -> None:
    role, user = names(tag)
    alt.session([
        'set rs=##class(%ResultSet).%New("%Library.SQLCatalogPriv:SQLRolePrivileges")',
        f'set sc=rs.Execute("{role}")',
        f'kill q while rs.Next() {{ set q($increment(q))="REVOKE "_rs.GetData(3)_" ON "_rs.GetData(2)_" FROM {role} '
        'CASCADE AS """_rs.GetData(4)_"""" }',
        'for i=1:1:$get(q) { set x=##class(%SQL.Statement).%ExecDirect(,q(i)) }'])
    alt.session([f'if ##class(Security.Users).Exists("{user}") do ##class(Security.Users).Delete("{user}")',
                 f'if ##class(Security.Roles).Exists("{role}") do ##class(Security.Roles).Delete("{role}")'],
                namespace="%SYS")


def call(user: str, expression: str) -> str:
    """Log in as `user`, then write `expression` (a %DynamicAbstractObject's JSON, or a value) between markers.
    Answers the text, or "EXC <error>" when it threw. `v` is the query vector [1, 0, ...] at ns460alt.DIM."""
    vector = "[" + ",".join(["1"] + ["0"] * (alt.DIM - 1)) + "]"
    line = " ".join([
        f'set ok=$SYSTEM.Security.Login("{user}","{PASSWORD}")',
        'write "<<LOGIN:",ok,"|",$Username,":LOGIN>>",!',
        f"try {{ set v={vector}, r={expression}",
        'write "<<R:",$select($isobject(r):r.%ToJSON(),1:r),":R>>",! }',
        'catch e { write "<<R:EXC ",e.DisplayString(),":R>>",! }',
        "halt",
    ])
    out = alt.session([line])
    assert alt.marked(out, "LOGIN") == f"1|{user}", out
    return alt.marked(out, "R")
