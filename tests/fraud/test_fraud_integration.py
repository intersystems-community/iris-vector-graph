"""
Integration Tests for Fraud Scoring System

Validates end-to-end fraud scoring with live IRIS database.
Prevents regression of datetime parameter issues and other IRIS SQL quirks.
"""

import pytest
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../src'))

try:
    import iris
    IRIS_AVAILABLE = True
except ImportError:
    IRIS_AVAILABLE = False

pytestmark = [
    pytest.mark.skipif(not IRIS_AVAILABLE, reason="IRIS module not available"),
    pytest.mark.requires_database,
    pytest.mark.integration
]


class TestDatabaseConnection:
    """Test IRIS database connectivity and basic operations"""

    def test_iris_import(self):
        """Verify iris module is importable"""
        import iris
        assert iris is not None

    def test_basic_query(self):
        """Verify basic SQL execution works"""
        result = iris.sql.exec("SELECT 1 AS test")
        rows = list(result)
        assert len(rows) == 1
        assert rows[0][0] == 1


class TestSchemaCreation:
    """Test fraud schema creation and table structure"""

    def test_gs_events_table_exists(self):
        """Verify gs_events table exists"""
        result = iris.sql.exec("""
            SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_NAME = 'gs_events'
        """)
        count = list(result)[0][0]
        assert count == 1, "gs_events table should exist"

    def test_gs_labels_table_exists(self):
        """Verify gs_labels table exists"""
        result = iris.sql.exec("""
            SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_NAME = 'gs_labels'
        """)
        count = list(result)[0][0]
        assert count == 1, "gs_labels table should exist"

    def test_gs_events_indexes_exist(self):
        """Verify gs_events indexes exist"""
        # Note: IRIS INFORMATION_SCHEMA for indexes may vary
        # This is a placeholder - actual implementation depends on IRIS version
        pass


class TestDatetimeParameterHandling:
    """Test IRIS SQL datetime handling (prevent regression of datetime parameter bug)"""

    def test_dateadd_function_works(self):
        """Verify DATEADD function works in queries"""
        result = iris.sql.exec("""
            SELECT COUNT(*) FROM gs_events
            WHERE ts >= DATEADD(hour, -24, CURRENT_TIMESTAMP)
        """)
        rows = list(result)
        assert len(rows) == 1
        # Count may be 0 if no recent data, but query should execute

    def test_python_datetime_not_used_as_parameter(self):
        """Document that Python datetime objects cannot be query parameters"""
        from datetime import datetime, timedelta

        # This pattern MUST NOT be used (would fail with "Invalid Dynamic Statement Parameter")
        # ts_24h = datetime.utcnow() - timedelta(hours=24)
        # result = iris.sql.exec("SELECT COUNT(*) WHERE ts >= ?", ts_24h)  # ❌ FAILS

        # Instead, use SQL DATEADD function (correct pattern)
        result = iris.sql.exec("""
            SELECT COUNT(*) FROM gs_events
            WHERE ts >= DATEADD(hour, -24, CURRENT_TIMESTAMP)
        """)  # ✅ WORKS
        rows = list(result)
        assert len(rows) == 1


class TestResultSetIteration:
    """Test IRIS result set iteration patterns (prevent fetchone() errors)"""

    def test_result_set_iteration_with_list(self):
        """Verify list() pattern works for consuming results"""
        result = iris.sql.exec("SELECT 1 AS col1, 2 AS col2")
        rows = list(result)
        assert len(rows) == 1
        assert rows[0][0] == 1
        assert rows[0][1] == 2

    def test_result_set_iteration_with_for_loop(self):
        """Verify for-loop iteration works"""
        result = iris.sql.exec("SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3")
        values = []
        for row in result:
            values.append(row[0])
        assert values == [1, 2, 3]

    def test_fetchone_not_available(self):
        """Document that fetchone() is NOT available on IRIS result sets"""
        result = iris.sql.exec("SELECT 1")
        # result.fetchone() would fail with "Property fetchone not found"
        # Instead, use list() or iterate directly
        assert not hasattr(result, 'fetchone'), "IRIS result sets don't support fetchone()"


class TestFeatureComputation:
    """Test fraud feature computation queries"""

    def test_rolling_24h_feature_query(self):
        """Test 24-hour rolling transaction count and sum"""
        result = iris.sql.exec("""
            SELECT COUNT(*) AS deg_24h, COALESCE(SUM(amount), 0.0) AS tx_amt_sum_24h
            FROM gs_events
            WHERE entity_id = ?
            AND ts >= DATEADD(hour, -24, CURRENT_TIMESTAMP)
        """, 'test_account')
        rows = list(result)
        assert len(rows) == 1
        deg_24h, tx_amt_sum_24h = rows[0]
        assert isinstance(deg_24h, int) or deg_24h == 0
        assert isinstance(tx_amt_sum_24h, (int, float))

    def test_rolling_7d_unique_devices(self):
        """Test 7-day unique device count"""
        result = iris.sql.exec("""
            SELECT COUNT(DISTINCT device_id)
            FROM gs_events
            WHERE entity_id = ?
            AND ts >= DATEADD(day, -7, CURRENT_TIMESTAMP)
            AND device_id IS NOT NULL
        """, 'test_account')
        rows = list(result)
        assert len(rows) == 1
        assert isinstance(rows[0][0], int) or rows[0][0] == 0


class TestDataInsertion:
    """Test transaction and label insertion"""

    def test_insert_event_with_dateadd(self):
        """Verify event insertion with DATEADD for timestamps"""
        test_id = f"test_insert_{pytest.current_test_id}"

        # Insert test event using DATEADD (not Python datetime)
        iris.sql.exec("""
            INSERT INTO gs_events (entity_id, kind, ts, amount)
            VALUES (?, 'tx', DATEADD(hour, -1, CURRENT_TIMESTAMP), ?)
        """, test_id, 100.0)

        # Verify insertion
        result = iris.sql.exec("""
            SELECT COUNT(*) FROM gs_events WHERE entity_id = ?
        """, test_id)
        count = list(result)[0][0]
        assert count == 1

        # Cleanup
        iris.sql.exec("DELETE FROM gs_events WHERE entity_id = ?", test_id)

    def test_insert_label(self):
        """Verify fraud label insertion"""
        test_id = f"test_label_{pytest.current_test_id}"

        # Insert test label
        iris.sql.exec("""
            INSERT INTO gs_labels (entity_id, label, label_ts, source)
            VALUES (?, 1, DATEADD(day, -1, CURRENT_TIMESTAMP), 'test')
        """, test_id)

        # Verify insertion
        result = iris.sql.exec("""
            SELECT label FROM gs_labels WHERE entity_id = ?
        """, test_id)
        rows = list(result)
        assert len(rows) == 1
        assert rows[0][0] == 1

        # Cleanup
        iris.sql.exec("DELETE FROM gs_labels WHERE entity_id = ?", test_id)


class TestQueryPerformance:
    """Test query performance meets targets"""

    def test_feature_query_performance(self):
        """Verify feature queries complete in <100ms"""
        import time

        test_account = 'acct:perf_test_001'

        start = time.perf_counter()
        result = iris.sql.exec("""
            SELECT COUNT(*), COALESCE(SUM(amount), 0.0)
            FROM gs_events
            WHERE entity_id = ?
            AND ts >= DATEADD(hour, -24, CURRENT_TIMESTAMP)
        """, test_account)
        list(result)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Should complete in <100ms (even with cold start)
        assert elapsed_ms < 100, f"Query took {elapsed_ms:.2f}ms (expected <100ms)"


# Pytest configuration
pytest.current_test_id = ""

@pytest.fixture(autouse=True)
def set_test_id(request):
    """Set unique test ID for each test"""
    pytest.current_test_id = request.node.nodeid.replace("::", "_").replace("/", "_")
