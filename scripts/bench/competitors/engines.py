"""Neo4j and Memgraph behind one small interface (both speak Bolt; the neo4j driver talks to either)."""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

NEO4J_URI_DEFAULT = "bolt://localhost:7688"
NEO4J_AUTH_DEFAULT = ("neo4j", "ivgbenchpw")
MEMGRAPH_URI_DEFAULT = "bolt://localhost:7689"
MEMGRAPH_AUTH_DEFAULT = ("", "")

ENGINE_NAMES = ("neo4j", "memgraph")


def q(name: str) -> str:
    """Backtick-quote a label, relationship type or property name unless it is a plain identifier."""
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return name
    return "`" + name.replace("`", "``") + "`"


class Engine:
    def __init__(self, name: str, uri: str, auth: Tuple[str, str]):
        from neo4j import GraphDatabase

        self.name, self.uri = name, uri
        self.driver = GraphDatabase.driver(uri, auth=auth, connection_timeout=15, max_connection_lifetime=3600)
        self.driver.verify_connectivity()
        self._session = None

    # -- running ------------------------------------------------------------

    def session(self):
        if self._session is None:
            self._session = self.driver.session()
        return self._session

    def run(self, cypher: str, params: Optional[Dict[str, Any]] = None, timeout_s: Optional[float] = None) -> List[Any]:
        """Execute and fetch every row (the rows cross the wire, as they do for a client of the engine)."""
        from neo4j import Query

        text: Any = cypher
        if timeout_s and self.name == "neo4j":
            text = Query(cypher, timeout=timeout_s)  # server-side transaction timeout
        res = self.session().run(text, params or {})
        return list(res)

    def scalar(self, cypher: str, params: Optional[Dict[str, Any]] = None, timeout_s: Optional[float] = None) -> Any:
        rows = self.run(cypher, params, timeout_s)
        return rows[0][0] if rows else None

    def reset_session(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            finally:
                self._session = None

    def close(self) -> None:
        self.reset_session()
        self.driver.close()

    # -- facts --------------------------------------------------------------

    def version(self) -> str:
        if self.name == "neo4j":
            rows = self.run("CALL dbms.components() YIELD name, versions, edition RETURN name, versions[0], edition")
            return f"{rows[0][0]} {rows[0][1]} {rows[0][2]}" if rows else "neo4j"
        rows = self.run("SHOW VERSION")
        return f"memgraph {rows[0][0]}" if rows else "memgraph"

    def gds_version(self) -> Optional[str]:
        if self.name != "neo4j":
            return None
        try:
            return str(self.scalar("RETURN gds.version()"))
        except Exception:
            return None

    def procedures(self) -> List[str]:
        """Names of the callable procedures (Memgraph: MAGE and built-ins)."""
        if self.name == "memgraph":
            return [r[0] for r in self.run("CALL mg.procedures() YIELD name RETURN name")]
        return [r[0] for r in self.run("SHOW PROCEDURES YIELD name RETURN name")]

    def node_count(self, label: Optional[str] = None) -> int:
        return int(self.scalar(f"MATCH (n{':' + q(label) if label else ''}) RETURN count(n)") or 0)

    def edge_count(self, label: Optional[str] = None) -> int:
        lab = f":{q(label)}" if label else ""
        return int(self.scalar(f"MATCH ({lab})-[r]->({lab}) RETURN count(r)") or 0)


def connect(name: str, uri: Optional[str] = None, user: Optional[str] = None, password: Optional[str] = None) -> Engine:
    if name == "neo4j":
        uri = uri or os.environ.get("NEO4J_URI", NEO4J_URI_DEFAULT)
        auth = (
            user or os.environ.get("NEO4J_USER", NEO4J_AUTH_DEFAULT[0]),
            password or os.environ.get("NEO4J_PASSWORD", NEO4J_AUTH_DEFAULT[1]),
        )
    elif name == "memgraph":
        uri = uri or os.environ.get("MEMGRAPH_URI", MEMGRAPH_URI_DEFAULT)
        auth = (user or "", password or "")
    else:
        raise ValueError(f"unknown engine {name!r}; known: {ENGINE_NAMES}")
    return Engine(name, uri, auth)
