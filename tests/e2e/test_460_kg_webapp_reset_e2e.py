"""4.6.0 R147 [SECURITY]: re-running `Graph.KG.Service.CreateWebApp` resets the `/kg` role and application.

R131 gave the application one role, `IVG_KG_<namespace>`. Three gaps were left:
- `GrantWebAppSql` only granted. A privilege the role held beyond the list (one a later release drops, or one an
  operator added) stayed. It now sets the role's privileges in the IVG Schema to exactly the list, whoever granted
  them: a REVOKE removes only the revoking user's own grants (SQLCODE 100, no error), so each one is revoked
  `AS` its grantor. A listed privilege held WITH GRANT OPTION is granted again without it.
- `EnsureWebAppRole` reset the role's resources but not its granted roles, so a role granted to it stayed.
- `CreateWebApp` on an existing application failed at `Security.Applications.Create`, after resetting the role: an
  application a 4.5.x release made kept `MatchRoles ":%All"`. It now updates the application, when it is IVG's (its
  dispatch class is `Graph.KG.Service`), and refuses any other application at that path, leaving it unchanged.

Run with IVG_NAMESPACE=IVG460FX8 (or another IVG460*/IVG262* scratch namespace; IVG Schema RAG). The application,
the role `IVG_KG_<NS>` and the role `<NS>_R147_EXTRA` this file makes are deleted at the end. `CreateWebApp` runs in
an `iris session` (as its OS user, irisowner); the extra privileges are granted over DB-API as _SYSTEM, a different
grantor, and one in the session, the same grantor.
"""

from __future__ import annotations

import pytest

from tests.e2e import ns460alt as alt

pytestmark = [pytest.mark.e2e, *alt.requires_namespace]

APP = "/" + alt.NS.lower() + "-r147-kg"
OTHER_APP = "/" + alt.NS.lower() + "-r147-other"
ROLE = "IVG_KG_" + alt.NS
EXTRA_ROLE = alt.NS + "_R147_EXTRA"
S = alt.SCHEMA

#: What CreateWebApp grants (R131), as SQLRolePrivileges reads it back: type, name, privilege, grant option.
LISTED = sorted([
    ("STORED PROCEDURE", f"{S}.kg_KNN_VEC", "EXECUTE", "No"),
    ("STORED PROCEDURE", f"{S}.kg_RRF_FUSE", "EXECUTE", "No"),
    ("STORED PROCEDURE", f"{S}.kg_TXT", "EXECUTE", "No"),
    ("TABLE", f"{S}.docs", "SELECT", "No"),
    ("TABLE", f"{S}.embedding_registry", "SELECT", "No"),
    ("TABLE", f"{S}.kg_NodeEmbeddings", "SELECT", "No"),
    ("TABLE", f"{S}.rdf_labels", "SELECT", "No"),
])


def _sys(lines: list) -> str:
    return alt.session(lines, namespace="%SYS")


def _privileges() -> list:
    """The role's SQL privileges in NS: (type, name, privilege, grant option), sorted."""
    out = alt.session([
        'set rs=##class(%ResultSet).%New("%Library.SQLCatalogPriv:SQLRolePrivileges")',
        f'set sc=rs.Execute("{ROLE}")',
        'write "<<PRIV:" while rs.Next() { write rs.GetData(1),"|",rs.GetData(2),"|",rs.GetData(3),"|",'
        'rs.GetData(5),";" } write ":PRIV>>",!'])
    return sorted(tuple(p.split("|")) for p in alt.marked(out, "PRIV").split(";") if p)


def _create_web_app(path: str) -> str:
    out = alt.session([f'set sc=##class(Graph.KG.Service).CreateWebApp("{path}")',
                       'write "<<CREATED:",$system.Status.IsOK(sc),"|",$system.Status.GetErrorText(sc),":CREATED>>",!'])
    return alt.marked(out, "CREATED")


def _app(path: str) -> dict:
    out = _sys([f'kill p set sc=##class(Security.Applications).Get("{path}",.p)',
                'write "<<APP:",sc,"|",$get(p("MatchRoles")),"|",$get(p("DispatchClass")),"|",$get(p("NameSpace")),'
                '"|",$get(p("Enabled")),"|",$get(p("AutheEnabled")),":APP>>",!'])
    got = alt.marked(out, "APP").split("|")
    return {"exists": got[0] == "1", "MatchRoles": got[1], "DispatchClass": got[2], "NameSpace": got[3],
            "Enabled": got[4], "AutheEnabled": got[5]}


def _make_app(path: str, props: dict) -> None:
    sets = " ".join(f'set p("{k}")="{v}"' for k, v in props.items())
    out = _sys([f'if ##class(Security.Applications).Exists("{path}") do ##class(Security.Applications).Delete("{path}")',
                f'kill p {sets} set sc=##class(Security.Applications).Create("{path}",.p)',
                'write "<<MADE:",$system.Status.GetErrorText(sc),":MADE>>",!'])
    assert alt.marked(out, "MADE") == "", out


def _drop_app(path: str) -> None:
    """Delete the application at `path`, so a test of the role does not also depend on the application's update."""
    _sys([f'if ##class(Security.Applications).Exists("{path}") do ##class(Security.Applications).Delete("{path}")'])


def _granted_roles() -> str:
    out = _sys([f'kill r set sc=##class(Security.Roles).Get("{ROLE}",.r)',
                'write "<<GR:",$get(r("GrantedRoles")),":GR>>",!'])
    return alt.marked(out, "GR")


def _drop_everything():
    # The role's SQL privileges go first: they live in the namespace, the role in %SYS.
    alt.session([
        'set rs=##class(%ResultSet).%New("%Library.SQLCatalogPriv:SQLRolePrivileges")',
        f'set sc=rs.Execute("{ROLE}")',
        'kill q while rs.Next() { set n=rs.GetData(2), pr=rs.GetData(3) '
        'if $length(n,".")=3 { set pr=pr_"("_$piece(n,".",3)_")", n=$piece(n,".",1,2) } '
        f'set q($increment(q))="REVOKE "_pr_" ON "_n_" FROM {ROLE} CASCADE AS """_rs.GetData(4)_"""" }}',
        'for i=1:1:$get(q) { set x=##class(%SQL.Statement).%ExecDirect(,q(i)) }'])
    _sys([f'if ##class(Security.Applications).Exists("{p}") do ##class(Security.Applications).Delete("{p}")'
          for p in (APP, OTHER_APP)]
         + [f'if ##class(Security.Roles).Exists("{r}") do ##class(Security.Roles).Delete("{r}")'
            for r in (ROLE, EXTRA_ROLE)])


@pytest.fixture(scope="module")
def ns():
    alt.make_namespace()
    conn = alt.connect()
    alt.engine(conn).initialize_schema()
    _drop_everything()
    yield conn
    _drop_everything()
    conn.close()


def test_a_rerun_revokes_every_privilege_beyond_the_list_whoever_granted_it(ns):
    _drop_app(APP)
    assert _create_web_app(APP).startswith("1|"), "the first CreateWebApp failed"
    assert _privileges() == LISTED
    cur = ns.cursor()
    # Granted by _SYSTEM over DB-API: not the session user CreateWebApp's REVOKE runs as.
    for grant in (f"GRANT INSERT ON {S}.docs TO {ROLE}",
                  f"GRANT UPDATE(text) ON {S}.docs TO {ROLE}",
                  f"GRANT SELECT ON {S}.rdf_edges TO {ROLE} WITH GRANT OPTION",
                  f"GRANT SELECT ON {S}.kg_NodeEmbeddings TO {ROLE} WITH GRANT OPTION"):
        cur.execute(grant)
    ns.commit()
    # And one by the session user itself.
    alt.session([f'set x=##class(%SQL.Statement).%ExecDirect(,"GRANT DELETE ON {S}.rdf_labels TO {ROLE}")'])
    held = _privileges()
    for extra in (("TABLE", f"{S}.docs", "INSERT", "No"), ("TABLE", f"{S}.docs.text", "UPDATE", "No"),
                  ("TABLE", f"{S}.rdf_edges", "SELECT", "Yes"), ("TABLE", f"{S}.kg_NodeEmbeddings", "SELECT", "Yes"),
                  ("TABLE", f"{S}.rdf_labels", "DELETE", "No")):
        assert extra in held, (extra, held)
    _drop_app(APP)

    assert _create_web_app(APP).startswith("1|")

    assert _privileges() == LISTED


def test_a_rerun_removes_a_role_granted_to_the_role(ns):
    _drop_app(APP)
    assert _create_web_app(APP).startswith("1|"), "the first CreateWebApp failed"
    out = _sys([f'if \'##class(Security.Roles).Exists("{EXTRA_ROLE}") '
                f'set sc=##class(Security.Roles).Create("{EXTRA_ROLE}","R147 test: holds nothing","")',
                f'kill r set r("GrantedRoles")="{EXTRA_ROLE}" set sc=##class(Security.Roles).Modify("{ROLE}",.r)',
                'write "<<MOD:",$system.Status.GetErrorText(sc),":MOD>>",!'])
    assert alt.marked(out, "MOD") == "", out
    assert _granted_roles() == EXTRA_ROLE
    _drop_app(APP)

    assert _create_web_app(APP).startswith("1|")

    assert _granted_roles() == ""


def test_a_rerun_updates_an_application_a_pre_460_release_made_with_all(ns):
    # What 4.5.x's CreateWebApp made, then switched off and set to another authentication, so every prop is checked.
    _make_app(APP, {"NameSpace": alt.NS, "DispatchClass": "Graph.KG.Service", "Enabled": 0, "MatchRoles": ":%All",
                    "AutheEnabled": 64})
    assert _app(APP)["MatchRoles"] == ":%All"

    created = _create_web_app(APP)

    assert created.startswith("1|"), created
    assert _app(APP) == {"exists": True, "MatchRoles": f":{ROLE}", "DispatchClass": "Graph.KG.Service",
                         "NameSpace": alt.NS, "Enabled": "1", "AutheEnabled": "32"}


def test_an_application_that_is_not_ivgs_is_refused_and_left_as_it_was(ns):
    _make_app(OTHER_APP, {"NameSpace": alt.NS, "Enabled": 0, "MatchRoles": ":%All", "AutheEnabled": 64,
                          "Description": "R147 test: not IVG's"})
    before = _app(OTHER_APP)

    created = _create_web_app(OTHER_APP)

    assert created.startswith("0|"), created
    assert OTHER_APP in created and "Graph.KG.Service" in created, created
    assert _app(OTHER_APP) == before
