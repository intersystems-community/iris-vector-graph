<!-- markdownlint-disable MD013 -->

# "Password change required" when connecting to IRIS

For developers and IRIS operators whose first connection to a new IRIS fails. IVG itself sets no password; this is
IRIS refusing a login whose password has expired.

## The symptom

`iris.connect(...)`, or `initialize_schema()` on a new connection, fails with an error that ends
`Password change required`. A new IRIS container's predefined accounts (`_SYSTEM`, `SuperUser`, `Admin`) are created
with the password `SYS` marked as expired, so the first login over the SuperServer is refused until the password is
changed or the expiry is lifted.

## The repository's container does not have it

The `docker-compose.yml` in this repository starts IRIS with

```yaml
command: --check-caps false -a "iris session iris -U%SYS '##class(Security.Users).UnExpireUserPasswords(\"*\")'"
```

which lifts the expiry of every account's password after IRIS starts, so `_SYSTEM` / `SYS` works from the first
connection ([Quickstart](QUICKSTART.md)). The volume keeps that state across `docker compose down` and `up`.

## Fixing an IRIS you started another way

Each fix runs in the `%SYS` namespace. Use one of them.

**Lift the expiry** (development and test only: every account keeps its current password and never has to change it):

```bash
docker compose exec iris iris session IRIS -U%SYS '##class(Security.Users).UnExpireUserPasswords("*")'
```

For a container you started with `docker run`, use `docker exec -it <container> iris session IRIS -U%SYS` and the
same method; `docker ps` lists the container names.

**Set a new password** for one account, in an IRIS terminal in `%SYS`:

<!-- doc-test: skip reason="changes _SYSTEM's password on the IRIS the doc test runs against" -->

```objectscript
Set props("Password") = "NewPassw0rd", props("ChangePassword") = 0
Set tSC = ##class(Security.Users).Modify("_SYSTEM", .props)
If tSC { Write "changed", ! } Else { Do $System.Status.DisplayError(tSC) }
```

Or log in to the Management Portal (`http://localhost:52773/csp/sys/UtilHome.csp` with the repository's compose file)
as `_SYSTEM` / `SYS`: it asks for a new password before it lets you in. The password policy (length and pattern) is
under System Administration > Security > System Security > Password Policy.

**Start over**, if the instance holds nothing you need: `docker compose down -v` deletes the IRIS volume, and the next
`docker compose up -d` starts a new instance with `_SYSTEM` / `SYS` and the expiry lifted.

## After a password change

Connect with the new password. IVG reads no password file; it uses the connection you pass it:

<!-- doc-test: skip reason="shows a password other than the doc-test container's" -->

```python
import iris
from iris_vector_graph import IRISGraphEngine

conn = iris.connect("localhost", 1972, "USER", "_SYSTEM", "NewPassw0rd")
engine = IRISGraphEngine(conn, embedding_dimension=768)
```

The `ivg` command and IVG's HTTP server read `IRIS_PASSWORD` (default `SYS`), and the server takes
`--iris-password` ([HTTP server](../ADMIN_API.md#starting-the-server)). Set it to the new password where you start
them.

## See also

- [InterSystems IRIS documentation: users and passwords](https://docs.intersystems.com/irislatest/csp/docbook/DocBook.UI.Page.cls?KEY=GSA_config_users)
