from unittest.mock import MagicMock, patch

from iris_vector_graph.engine import IRISGraphEngine


def _engine():
    eng = IRISGraphEngine.__new__(IRISGraphEngine)
    eng._arno_capabilities = {"algorithms": ["khop"]}
    return eng


class TestKhopHops1FastPath:
    def test_hops1_skips_arno_path(self):
        eng = _engine()
        with patch.object(eng, "_detect_arno", return_value=True) as det, \
             patch.object(eng, "_arno_call") as arno, \
             patch.object(eng, "_khop_fallback", return_value={"nodes": [], "edges": []}) as fb:
            eng.khop("seed", hops=1)
        arno.assert_not_called()
        fb.assert_called_once_with("seed", 1, 500)

    def test_hops2_uses_arno_path_when_available(self):
        eng = _engine()
        with patch.object(eng, "_detect_arno", return_value=True), \
             patch.object(eng, "_arno_call", return_value='{"nodes":["a"],"edges":[]}') as arno, \
             patch.object(eng, "_khop_fallback") as fb:
            result = eng.khop("seed", hops=2)
        arno.assert_called_once()
        fb.assert_not_called()
        assert result == {"nodes": ["a"], "edges": []}

    def test_hops2_falls_back_when_arno_absent(self):
        eng = _engine()
        with patch.object(eng, "_detect_arno", return_value=False), \
             patch.object(eng, "_arno_call") as arno, \
             patch.object(eng, "_khop_fallback", return_value={"nodes": [], "edges": []}) as fb:
            eng.khop("seed", hops=2)
        arno.assert_not_called()
        fb.assert_called_once()

    def test_hops2_arno_error_falls_back(self):
        eng = _engine()
        with patch.object(eng, "_detect_arno", return_value=True), \
             patch.object(eng, "_arno_call", return_value='{"error":"boom"}'), \
             patch.object(eng, "_khop_fallback", return_value={"nodes": [], "edges": []}) as fb:
            eng.khop("seed", hops=2)
        fb.assert_called_once()
