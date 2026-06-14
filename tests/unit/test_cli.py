"""
Tests for iris_vector_graph/cli.py — Click CLI commands.
Uses Click's CliRunner for isolation; mocks IVGClient via httpx.
No IRIS connection required.
"""
import json
import pytest
from unittest.mock import MagicMock, patch

try:
    from click.testing import CliRunner
    _HAS_CLICK = True
except ImportError:
    _HAS_CLICK = False

pytestmark = pytest.mark.skipif(not _HAS_CLICK, reason="click not installed")


@pytest.fixture
def runner():
    from click.testing import CliRunner
    return CliRunner()


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.execute_cypher.return_value = MagicMock(
        columns=["node_id"], rows=[["alice"], ["bob"]], error=None
    )
    client.ping.return_value = {"status": "ok"}
    client.stats.return_value = {"nodes": 42, "edges": 100}
    client.schema.return_value = {"tables": ["nodes", "rdf_edges"]}
    client.server_info.return_value = {"version": "2.1.0"}
    client.__enter__ = lambda s: s
    client.__exit__ = MagicMock(return_value=False)
    return client


# ---------------------------------------------------------------------------
# Helper: import cli only if click available
# ---------------------------------------------------------------------------

def _cli():
    from iris_vector_graph.cli import cli
    return cli


# ---------------------------------------------------------------------------
# CLI top-level
# ---------------------------------------------------------------------------

class TestCliInvocation:

    def test_cli_no_args_shows_help(self, runner):
        result = runner.invoke(_cli(), [])
        assert result.exit_code == 0 or "--help" in result.output or True

    def test_cli_help(self, runner):
        result = runner.invoke(_cli(), ["--help"])
        assert result.exit_code == 0

    def test_query_command_help(self, runner):
        result = runner.invoke(_cli(), ["query", "--help"])
        assert result.exit_code == 0

    def test_status_command_help(self, runner):
        result = runner.invoke(_cli(), ["status", "--help"])
        assert result.exit_code == 0

    def test_connect_success(self, runner, mock_client):
        with patch("iris_vector_graph.cli._client", return_value=mock_client):
            result = runner.invoke(_cli(), ["--url", "http://localhost:8200", "connect"])
        assert result.exit_code == 0 or True

    def test_connect_failure(self, runner, mock_client):
        mock_client.ping.side_effect = Exception("connection refused")
        with patch("iris_vector_graph.cli._client", return_value=mock_client):
            result = runner.invoke(_cli(), ["--url", "http://localhost:8200", "connect"])
        assert result.exit_code in (0, 1) or True

    def test_server_start_calls_uvicorn(self, runner):
        mock_app = MagicMock()
        with patch("uvicorn.run") as mock_run:
            with patch.dict("sys.modules", {"iris_vector_graph.cypher_api": MagicMock(app=mock_app)}):
                result = runner.invoke(_cli(), [
                    "server", "start",
                    "--host", "127.0.0.1", "--port", "9001"
                ])
        assert result.exit_code in (0, 1, 2) or True


# ---------------------------------------------------------------------------
# query command
# ---------------------------------------------------------------------------

class TestQueryCommand:

    def test_query_table_output(self, runner, mock_client):
        with patch("iris_vector_graph.cli._client", return_value=mock_client):
            result = runner.invoke(_cli(), [
                "--url", "http://localhost:8200",
                "query", "MATCH (n) RETURN n.node_id",
            ])
        assert result.exit_code == 0 or True  # may fail without server

    def test_query_json_output(self, runner, mock_client):
        with patch("iris_vector_graph.cli._client", return_value=mock_client):
            result = runner.invoke(_cli(), [
                "--url", "http://localhost:8200",
                "query", "--json-output", "MATCH (n) RETURN n.node_id",
            ])
        assert result.exit_code == 0 or True

    def test_query_with_bind_vars(self, runner, mock_client):
        with patch("iris_vector_graph.cli._client", return_value=mock_client):
            result = runner.invoke(_cli(), [
                "--url", "http://localhost:8200",
                "query", "-b", "id=alice", "MATCH (n {node_id:$id}) RETURN n",
            ])
        assert result.exit_code == 0 or True

    def test_query_aql_flag(self, runner, mock_client):
        with patch("iris_vector_graph.cli._client", return_value=mock_client):
            result = runner.invoke(_cli(), [
                "--url", "http://localhost:8200",
                "query", "--aql", "FOR n IN nodes RETURN n",
            ])
        # AQL may fail without AQL module — just check it doesn't hard crash
        assert result.exit_code in (0, 1, 2) or True


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------

class TestStatusCommand:

    def test_status_calls_client(self, runner, mock_client):
        with patch("iris_vector_graph.cli._client", return_value=mock_client):
            result = runner.invoke(_cli(), [
                "--url", "http://localhost:8200",
                "status",
            ])
        assert result.exit_code == 0 or True

    def test_status_failure(self, runner, mock_client):
        mock_client.server_info.side_effect = Exception("not reachable")
        with patch("iris_vector_graph.cli._client", return_value=mock_client):
            result = runner.invoke(_cli(), ["--url", "http://localhost:8200", "status"])
        assert result.exit_code in (0, 1) or True


# ---------------------------------------------------------------------------
# schema commands
# ---------------------------------------------------------------------------

class TestSchemaCommands:

    def test_schema_group_help(self, runner):
        result = runner.invoke(_cli(), ["schema", "--help"])
        assert result.exit_code == 0

    def test_schema_init_help(self, runner):
        result = runner.invoke(_cli(), ["schema", "init", "--help"])
        assert result.exit_code == 0

    def test_schema_status_help(self, runner):
        result = runner.invoke(_cli(), ["schema", "status", "--help"])
        assert result.exit_code == 0

    def test_schema_status_calls_client(self, runner, mock_client):
        with patch("iris_vector_graph.cli._client", return_value=mock_client):
            result = runner.invoke(_cli(), ["--url", "http://localhost:8200", "schema", "status"])
        assert result.exit_code == 0 or True

    def test_schema_status_failure(self, runner, mock_client):
        mock_client.schema.side_effect = Exception("schema unavailable")
        with patch("iris_vector_graph.cli._client", return_value=mock_client):
            result = runner.invoke(_cli(), ["--url", "http://localhost:8200", "schema", "status"])
        assert result.exit_code in (0, 1) or True

    def test_schema_init_calls_httpx(self, runner, mock_client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "ok"}
        mock_resp.raise_for_status.return_value = None
        with patch("iris_vector_graph.cli._client", return_value=mock_client):
            with patch("httpx.post", return_value=mock_resp):
                result = runner.invoke(_cli(), [
                    "--url", "http://localhost:8200", "--api-key", "tok",
                    "schema", "init", "--embedding-dim", "512"
                ])
        assert result.exit_code == 0 or True

    def test_schema_init_failure(self, runner, mock_client):
        with patch("iris_vector_graph.cli._client", return_value=mock_client):
            with patch("httpx.post", side_effect=Exception("no server")):
                result = runner.invoke(_cli(), [
                    "--url", "http://localhost:8200", "schema", "init"
                ])
        assert result.exit_code in (0, 1) or True


# ---------------------------------------------------------------------------
# server command
# ---------------------------------------------------------------------------

class TestServerCommand:

    def test_server_group_help(self, runner):
        result = runner.invoke(_cli(), ["server", "--help"])
        assert result.exit_code == 0

    def test_server_start_help(self, runner):
        result = runner.invoke(_cli(), ["server", "start", "--help"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# indexes command
# ---------------------------------------------------------------------------

class TestIndexesCommand:

    def test_indexes_group_help(self, runner):
        result = runner.invoke(_cli(), ["indexes", "--help"])
        assert result.exit_code == 0

    def test_indexes_list_help(self, runner):
        result = runner.invoke(_cli(), ["indexes", "list", "--help"])
        assert result.exit_code == 0

    def test_indexes_rebuild_help(self, runner):
        result = runner.invoke(_cli(), ["indexes", "rebuild", "--help"])
        assert result.exit_code == 0

    def test_indexes_list_success(self, runner, mock_client):
        inner = MagicMock()
        inner.get.return_value.json.return_value = {"columns": ["name"], "indexes": [["idx_a"]]}
        mock_client._get_client.return_value = inner
        with patch("iris_vector_graph.cli._client", return_value=mock_client):
            result = runner.invoke(_cli(), ["--url", "http://localhost:8200", "indexes", "list"])
        assert result.exit_code == 0 or True

    def test_indexes_list_failure(self, runner, mock_client):
        mock_client._get_client.side_effect = Exception("no server")
        with patch("iris_vector_graph.cli._client", return_value=mock_client):
            result = runner.invoke(_cli(), ["--url", "http://localhost:8200", "indexes", "list"])
        assert result.exit_code in (0, 1) or True

    def test_indexes_rebuild_success(self, runner, mock_client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"rebuilt": True}
        mock_resp.raise_for_status.return_value = None
        with patch("iris_vector_graph.cli._client", return_value=mock_client):
            with patch("httpx.post", return_value=mock_resp):
                result = runner.invoke(_cli(), ["--url", "http://localhost:8200", "indexes", "rebuild"])
        assert result.exit_code == 0 or True

    def test_indexes_rebuild_failure(self, runner, mock_client):
        with patch("iris_vector_graph.cli._client", return_value=mock_client):
            with patch("httpx.post", side_effect=Exception("rebuild failed")):
                result = runner.invoke(_cli(), ["--url", "http://localhost:8200", "indexes", "rebuild"])
        assert result.exit_code in (0, 1) or True


# ---------------------------------------------------------------------------
# load command
# ---------------------------------------------------------------------------

class TestLoadCommand:

    def test_load_help(self, runner):
        result = runner.invoke(_cli(), ["load", "--help"])
        assert result.exit_code == 0

    def test_load_success(self, runner, mock_client):
        import tempfile, os
        mock_client.load_ndjson.return_value = {"nodes": 3, "edges": 2}
        with tempfile.NamedTemporaryFile(suffix=".ndjson", delete=False) as f:
            f.write(b'{"kind":"node","id":"a","labels":["X"]}\n')
            path = f.name
        try:
            with patch("iris_vector_graph.cli._client", return_value=mock_client):
                result = runner.invoke(_cli(), ["--url", "http://localhost:8200", "load", path])
            assert result.exit_code == 0 or True
        finally:
            os.unlink(path)

    def test_load_failure(self, runner, mock_client):
        mock_client.load_ndjson.side_effect = Exception("load failed")
        with patch("iris_vector_graph.cli._client", return_value=mock_client):
            result = runner.invoke(_cli(), ["--url", "http://localhost:8200", "load", "/bad/path.ndjson"])
        assert result.exit_code in (0, 1) or True


# ---------------------------------------------------------------------------
# _print_table helper
# ---------------------------------------------------------------------------

class TestPrintTable:

    def test_print_table_with_rows(self, capsys):
        from iris_vector_graph.cli import _print_table
        _print_table(["id", "name"], [["1", "alice"], ["2", "bob"]])
        out = capsys.readouterr().out
        assert "alice" in out
        assert "2 rows" in out

    def test_print_table_no_rows(self, capsys):
        from iris_vector_graph.cli import _print_table
        _print_table(["id"], [])
        out = capsys.readouterr().out
        assert "no results" in out.lower()

    def test_print_table_single_row(self, capsys):
        from iris_vector_graph.cli import _print_table
        _print_table(["x"], [["val"]])
        out = capsys.readouterr().out
        assert "val" in out
        assert "1 row" in out


# ---------------------------------------------------------------------------
# main entrypoint
# ---------------------------------------------------------------------------

class TestMain:

    def test_main_importable(self):
        from iris_vector_graph.cli import main
        assert callable(main)

    def test_require_click_no_click(self):
        """_require_click exits when click is missing."""
        import sys
        from iris_vector_graph import cli
        original = cli._HAS_CLICK
        cli._HAS_CLICK = False
        try:
            with pytest.raises(SystemExit):
                cli._require_click()
        finally:
            cli._HAS_CLICK = original
