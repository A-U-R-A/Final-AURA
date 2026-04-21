"""
Tests for app/database.py — every read/write method, schema constraints,
PRAGMA settings, and data-integrity invariants.
"""

import json
import sqlite3
import pytest
from datetime import datetime
from app import constants
from app.database import Database


@pytest.fixture
def db(tmp_path):
    """Fresh isolated database for each test."""
    return Database(str(tmp_path / "aura.db"))


@pytest.fixture
def nominal_reading():
    return {p: (lo + hi) / 2 for p, (lo, hi) in constants.PARAMETER_NOMINAL_RANGES.items()}


@pytest.fixture
def location():
    return constants.LOCATIONS[0]


# ── SCHEMA & PRAGMAS ─────────────────────────────────────────────────────────

class TestSchema:
    def test_tables_created(self, db):
        conn = sqlite3.connect(db.db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        expected = {"faults", "locations", "generated_data", "anomaly_labels", "alerts"}
        assert expected.issubset(tables)

    def test_wal_mode(self, db):
        conn = sqlite3.connect(db.db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"

    def test_busy_timeout(self, db):
        conn = sqlite3.connect(db.db_path)
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        conn.close()
        assert timeout == 5000

    def test_locations_seeded(self, db):
        conn = sqlite3.connect(db.db_path)
        rows = conn.execute("SELECT location_name FROM locations").fetchall()
        conn.close()
        names = {r[0] for r in rows}
        assert set(constants.LOCATIONS).issubset(names)

    def test_faults_seeded(self, db):
        conn = sqlite3.connect(db.db_path)
        rows = conn.execute("SELECT fault_name FROM faults").fetchall()
        conn.close()
        names = {r[0] for r in rows}
        assert set(constants.FAULT_IMPACT_SEVERITY.keys()).issubset(names)

    def test_foreign_keys_enforced(self, db):
        # PRAGMA foreign_keys is per-connection; check via Database._connect()
        with db._connect() as conn:
            fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1


# ── INSERT / READ ROUNDTRIP ────────────────────────────────────────────────

class TestDataInsertRead:
    def test_insert_data_returns_int(self, db, nominal_reading, location):
        ts = datetime.now().isoformat()
        row_id = db.insert_data(nominal_reading, location, ts)
        assert isinstance(row_id, int)
        assert row_id > 0

    def test_insert_data_increments(self, db, nominal_reading, location):
        ts = datetime.now().isoformat()
        id1 = db.insert_data(nominal_reading, location, ts)
        id2 = db.insert_data(nominal_reading, location, ts)
        assert id2 > id1

    def test_insert_label_nominal(self, db, nominal_reading, location):
        row_id = db.insert_data(nominal_reading, location, datetime.now().isoformat())
        db.insert_label(row_id, 1, None)  # should not raise

    def test_insert_label_anomalous_with_rf(self, db, nominal_reading, location):
        row_id = db.insert_data(nominal_reading, location, datetime.now().isoformat())
        rf = {"Cabin Leak": 0.7, "O2 Leak": 0.3}
        db.insert_label(row_id, -1, rf)

    def test_get_latest_reading_returns_dict(self, db, nominal_reading, location):
        ts = datetime.now().isoformat()
        row_id = db.insert_data(nominal_reading, location, ts)
        db.insert_label(row_id, 1, None)
        result = db.get_latest_reading(location)
        assert isinstance(result, dict)
        assert "data" in result
        assert "timestamp" in result

    def test_get_latest_reading_data_matches(self, db, nominal_reading, location):
        ts = datetime.now().isoformat()
        row_id = db.insert_data(nominal_reading, location, ts)
        db.insert_label(row_id, 1, None)
        result = db.get_latest_reading(location)
        for param, val in nominal_reading.items():
            assert abs(result["data"][param] - val) < 1e-9

    def test_get_latest_reading_includes_labels(self, db, nominal_reading, location):
        ts = datetime.now().isoformat()
        row_id = db.insert_data(nominal_reading, location, ts)
        db.insert_label(row_id, -1, {"Cabin Leak": 0.9})
        result = db.get_latest_reading(location)
        assert result["if_label"] == -1
        assert isinstance(result["rf_classification"], dict)

    def test_get_latest_reading_empty_db(self, db):
        loc = constants.LOCATIONS[-1]
        result = db.get_latest_reading(loc)
        assert result == {}

    def test_get_history_returns_list(self, db, nominal_reading, location):
        ts = datetime.now().isoformat()
        for _ in range(5):
            row_id = db.insert_data(nominal_reading, location, ts)
            db.insert_label(row_id, 1, None)
        param = list(constants.PARAMETER_NOMINAL_RANGES.keys())[0]
        history = db.get_history(location, param, n=5)
        assert isinstance(history, list)
        assert len(history) <= 5

    def test_get_history_items_have_required_keys(self, db, nominal_reading, location):
        ts = datetime.now().isoformat()
        row_id = db.insert_data(nominal_reading, location, ts)
        db.insert_label(row_id, 1, None)
        param = list(constants.PARAMETER_NOMINAL_RANGES.keys())[0]
        history = db.get_history(location, param, n=10)
        for item in history:
            assert "timestamp" in item
            assert "value" in item
            assert "anomalous" in item

    def test_get_recent_readings_returns_list(self, db, nominal_reading, location):
        ts = datetime.now().isoformat()
        for _ in range(3):
            row_id = db.insert_data(nominal_reading, location, ts)
            db.insert_label(row_id, 1, None)
        readings = db.get_recent_readings(location, n=3)
        assert isinstance(readings, list)

    def test_get_recent_readings_items_have_required_keys(self, db, nominal_reading, location):
        ts = datetime.now().isoformat()
        row_id = db.insert_data(nominal_reading, location, ts)
        db.insert_label(row_id, 1, None)
        readings = db.get_recent_readings(location, n=5)
        for r in readings:
            assert "id" in r
            assert "data" in r
            assert "timestamp" in r
            assert "if_label" in r

    def test_get_row_count(self, db, nominal_reading, location):
        before = db.get_row_count()
        ts = datetime.now().isoformat()
        db.insert_data(nominal_reading, location, ts)
        assert db.get_row_count() == before + 1


# ── FAULTS ────────────────────────────────────────────────────────────────────

class TestFaults:
    def test_insert_and_get_active_fault(self, db, location):
        fault = list(constants.FAULT_IMPACT_SEVERITY.keys())[0]
        db.insert_fault(fault, location)
        assert db.get_active_fault(location) == fault

    def test_no_active_fault_returns_none(self, db, location):
        db.clear_fault_for_location(location)
        assert db.get_active_fault(location) is None

    def test_clear_fault_for_location(self, db, location):
        fault = list(constants.FAULT_IMPACT_SEVERITY.keys())[0]
        db.insert_fault(fault, location)
        db.clear_fault_for_location(location)
        assert db.get_active_fault(location) is None

    def test_clear_faults_resets_all(self, db):
        fault = list(constants.FAULT_IMPACT_SEVERITY.keys())[0]
        for loc in constants.LOCATIONS[:3]:
            db.insert_fault(fault, loc)
        db.clear_faults()
        for loc in constants.LOCATIONS[:3]:
            assert db.get_active_fault(loc) is None

    def test_insert_unknown_fault_raises(self, db, location):
        with pytest.raises(ValueError):
            db.insert_fault("NonExistentFault", location)

    def test_insert_unknown_location_raises(self, db):
        fault = list(constants.FAULT_IMPACT_SEVERITY.keys())[0]
        with pytest.raises(ValueError):
            db.insert_fault(fault, "Nonexistent Module")

    def test_can_replace_fault(self, db, location):
        faults = list(constants.FAULT_IMPACT_SEVERITY.keys())
        db.insert_fault(faults[0], location)
        db.insert_fault(faults[1], location)
        assert db.get_active_fault(location) == faults[1]


# ── ALERTS ────────────────────────────────────────────────────────────────────

class TestAlerts:
    def _insert_alert(self, db, location, severity="WARNING"):
        return db.insert_alert(
            location_name=location,
            timestamp=datetime.now().isoformat(),
            severity=severity,
            fault_type="Cabin Leak",
            top_probability=0.85,
            sensor_data={"O2 partial pressure": 20.0},
        )

    def test_insert_alert_returns_int(self, db, location):
        aid = self._insert_alert(db, location)
        assert isinstance(aid, int) and aid > 0

    def test_get_alerts_returns_list(self, db, location):
        self._insert_alert(db, location)
        alerts = db.get_alerts()
        assert isinstance(alerts, list)
        assert len(alerts) >= 1

    def test_alert_has_required_keys(self, db, location):
        self._insert_alert(db, location)
        alerts = db.get_alerts(limit=1)
        a = alerts[0]
        for k in ("id", "location", "timestamp", "severity", "fault_type",
                   "top_probability", "acknowledged"):
            assert k in a, f"Alert missing key {k!r}"

    def test_get_alerts_filter_by_location(self, db):
        loc1, loc2 = constants.LOCATIONS[0], constants.LOCATIONS[1]
        self._insert_alert(db, loc1)
        self._insert_alert(db, loc2)
        alerts = db.get_alerts(location_name=loc1)
        assert all(a["location"] == loc1 for a in alerts)

    def test_get_alerts_unacked_only(self, db, location):
        aid = self._insert_alert(db, location)
        db.acknowledge_alert(aid)
        unacked = db.get_alerts(unacked_only=True)
        assert not any(a["id"] == aid for a in unacked)

    def test_acknowledge_alert(self, db, location):
        aid = self._insert_alert(db, location)
        db.acknowledge_alert(aid)
        alerts = db.get_alerts()
        target = next(a for a in alerts if a["id"] == aid)
        assert target["acknowledged"] is True

    def test_acknowledge_all_alerts(self, db, location):
        self._insert_alert(db, location)
        self._insert_alert(db, location)
        db.acknowledge_all_alerts()
        unacked = db.get_alerts(unacked_only=True)
        assert unacked == []

    def test_get_alert_count_total(self, db, location):
        before = db.get_alert_count(unacked_only=False)
        self._insert_alert(db, location)
        assert db.get_alert_count(unacked_only=False) == before + 1

    def test_get_alert_count_unacked(self, db, location):
        before = db.get_alert_count(unacked_only=True)
        self._insert_alert(db, location)
        assert db.get_alert_count(unacked_only=True) == before + 1

    def test_clear_alerts(self, db, location):
        self._insert_alert(db, location)
        db.clear_alerts()
        assert db.get_alert_count(unacked_only=False) == 0


# ── DATA RETENTION & EXPORT ───────────────────────────────────────────────────

class TestRetentionExport:
    def _fill_rows(self, db, location, n):
        reading = {p: (lo + hi) / 2 for p, (lo, hi) in constants.PARAMETER_NOMINAL_RANGES.items()}
        ts = datetime.now().isoformat()
        ids = []
        for _ in range(n):
            row_id = db.insert_data(reading, location, ts)
            db.insert_label(row_id, 1, None)
            ids.append(row_id)
        return ids

    def test_get_export_max_id_empty(self, db):
        db.clear_data()
        result = db.get_export_max_id()
        assert result is None

    def test_get_export_max_id_after_insert(self, db):
        loc = constants.LOCATIONS[0]
        ids = self._fill_rows(db, loc, 3)
        max_id = db.get_export_max_id()
        assert max_id == max(ids)

    def test_get_row_count_up_to(self, db):
        db.clear_data()
        loc = constants.LOCATIONS[0]
        ids = self._fill_rows(db, loc, 10)
        mid = ids[4]
        count = db.get_row_count_up_to(mid)
        assert count == 5

    def test_clear_exported_data(self, db):
        db.clear_data()
        loc = constants.LOCATIONS[0]
        ids = self._fill_rows(db, loc, 10)
        watermark = ids[4]
        after_ids = ids[5:]
        deleted = db.clear_exported_data(watermark)
        assert deleted == 5
        # Rows after watermark must still exist
        for rid in after_ids:
            conn = sqlite3.connect(db.db_path)
            row = conn.execute("SELECT id FROM generated_data WHERE id=?", (rid,)).fetchone()
            conn.close()
            assert row is not None, f"Row {rid} should not have been deleted"

    def test_prune_old_rows_removes_oldest(self, db):
        db.clear_data()
        loc = constants.LOCATIONS[0]
        ids = self._fill_rows(db, loc, 20)
        pruned = db.prune_old_rows(max_rows=10)
        assert pruned == 10
        assert db.get_row_count() == 10
        # Newest rows remain
        conn = sqlite3.connect(db.db_path)
        remaining = {r[0] for r in conn.execute("SELECT id FROM generated_data").fetchall()}
        conn.close()
        for rid in ids[-10:]:
            assert rid in remaining

    def test_prune_old_rows_no_op_when_under_limit(self, db):
        db.clear_data()
        loc = constants.LOCATIONS[0]
        self._fill_rows(db, loc, 5)
        pruned = db.prune_old_rows(max_rows=100)
        assert pruned == 0

    def test_clear_data_removes_all(self, db):
        loc = constants.LOCATIONS[0]
        reading = {p: 1.0 for p in constants.PARAMETER_NOMINAL_RANGES}
        db.insert_data(reading, loc, datetime.now().isoformat())
        db.clear_data()
        assert db.get_row_count() == 0


# ── LOCATION STATES ───────────────────────────────────────────────────────────

class TestLocationStates:
    def test_get_all_location_states_covers_all_locations(self, db):
        states = db.get_all_location_states()
        for loc in constants.LOCATIONS:
            assert loc in states

    def test_location_state_has_required_keys(self, db):
        states = db.get_all_location_states()
        for loc, state in states.items():
            assert "active_fault" in state
            assert "is_anomalous" in state
