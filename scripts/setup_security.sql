-- Roles for reading and writing an IVG graph: the SQL half of docs/ADMIN_GUIDE.md section 3, "Privileges".
-- Run it in the namespace IVG was set up in, as a user who may create roles. Graph_KG is the default IVG Schema;
-- under another one, change the schema name.
--
-- SQL cannot grant resources, and both roles need them too (the Portal's Security > Roles, or Security.Roles in %SYS):
--   graph_reader: <database resource>:R,  <routines database resource>:R, %Native_ClassExecution:U, %Native_GlobalAccess:U
--   graph_writer: <database resource>:RW, <routines database resource>:R, %Native_ClassExecution:U, %Native_GlobalAccess:U
-- <database resource> is the resource of the namespace's globals database, <routines database resource> that of its
-- routines database (the same one when the namespace has one database). Section 3 says how to look them up.

-- Graph Reader Role: every IVG table and function, read only
CREATE ROLE graph_reader;
GRANT SELECT ON SCHEMA Graph_KG TO graph_reader;
GRANT EXECUTE ON SCHEMA Graph_KG TO graph_reader;

-- Graph Writer Role: the same, and writes
CREATE ROLE graph_writer;
GRANT SELECT ON SCHEMA Graph_KG TO graph_writer;
GRANT EXECUTE ON SCHEMA Graph_KG TO graph_writer;
GRANT INSERT, UPDATE, DELETE ON SCHEMA Graph_KG TO graph_writer;
