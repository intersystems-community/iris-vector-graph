"""The TCK harness connects to the namespace named by IVG_TCK_NAMESPACE (default USER)."""
import socket
from unittest.mock import MagicMock, patch


class TestTckNamespaceEnv:
    def test_default_is_user(self, monkeypatch):
        from tests.tck.environment import _tck_namespace

        monkeypatch.delenv("IVG_TCK_NAMESPACE", raising=False)
        assert _tck_namespace() == "USER"

    def test_env_overrides(self, monkeypatch):
        from tests.tck.environment import _tck_namespace

        monkeypatch.setenv("IVG_TCK_NAMESPACE", "TCKA")
        assert _tck_namespace() == "TCKA"

    def test_blank_env_falls_back_to_user(self, monkeypatch):
        from tests.tck.environment import _tck_namespace

        monkeypatch.setenv("IVG_TCK_NAMESPACE", "  ")
        assert _tck_namespace() == "USER"

    def test_orbstack_connect_uses_env_namespace(self, monkeypatch):
        from tests.tck import environment

        monkeypatch.setenv("IVG_TCK_NAMESPACE", "TCKB")
        fake = MagicMock()
        with patch.object(socket, "gethostbyname", return_value="10.0.0.1"), \
             patch("iris.dbapi.connect", fake):
            environment._connect("ivg-iris-enterprise", 31972)
        assert fake.call_args.kwargs["namespace"] == "TCKB"

    def test_localhost_connect_uses_env_namespace(self, monkeypatch):
        from tests.tck import environment

        monkeypatch.setenv("IVG_TCK_NAMESPACE", "TCKC")
        fake = MagicMock()
        with patch.object(socket, "gethostbyname", side_effect=socket.gaierror), \
             patch("iris.dbapi.connect", fake):
            environment._connect("ivg-iris-enterprise", 31972)
        assert fake.call_args.kwargs["namespace"] == "TCKC"

    def test_no_devtester_fallback_for_non_user_namespace(self, monkeypatch):
        import pytest
        from tests.tck import environment

        monkeypatch.setenv("IVG_TCK_NAMESPACE", "TCKD")
        with patch.object(socket, "gethostbyname", side_effect=socket.gaierror), \
             patch("iris.dbapi.connect", side_effect=OSError("refused")):
            with pytest.raises(RuntimeError, match="TCKD"):
                environment._connect("ivg-iris-enterprise", 31972)
