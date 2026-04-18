import sqlite3
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import constants


class Database:
    """
    SQLite persistence layer for AURA.

    Schema (created on first run):
        faults          — lookup table of fault names
        locations       — ISS module registry; holds current_fault_id FK
        generated_data  — timestamped sensor readings (JSON blob per row)
        anomaly_labels  — IF label + RF classification attached to each reading
        alerts          — fired alert log with severity and acknowledgement state

    All connections use WAL journal mode for concurrent read/write safety
    (the background loop writes while REST handlers read simultaneously).
    """

    def __init__(self, db_path: str = constants.DATABASE_PATH):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self):
        """Open a connection, yield it, then close — used as a with-block context manager.
        check_same_thread=False is safe because each call opens its own connection object.
        Row factory enables column-name access (row["col"]) instead of positional indexing."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self):
        """Create tables if they don't exist and seed fault/location rows from constants.
        Idempotent — safe to call every startup."""
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS faults (
                    id   INTEGER PRIMARY KEY AUTOINCREMENT,
                    fault_name TEXT NOT NULL UNIQUE
                );

                CREATE TABLE IF NOT EXISTS locations (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    location_name   TEXT NOT NULL UNIQUE,
                    current_fault_id INTEGER REFERENCES faults(id)
                );

                CREATE TABLE IF NOT EXISTS generated_data (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    location_id INTEGER NOT NULL REFERENCES locations(id),
                    data        TEXT NOT NULL,
                    timestamp   TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_data_location_ts
                    ON generated_data(location_id, timestamp DESC);

                CREATE TABLE IF NOT EXISTS anomaly_labels (
                    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
                    data_row_id                 INTEGER NOT NULL UNIQUE REFERENCES generated_data(id),
                    isolation_forest_label      INTEGER NOT NULL,
                    random_forest_classification TEXT
                );

                CREATE TABLE IF NOT EXISTS alerts (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    location_name   TEXT NOT NULL,
                    timestamp       TEXT NOT NULL,
                    severity        TEXT NOT NULL DEFAULT 'WARNING',
                    fault_type      TEXT,
                    top_probability REAL,
                    sensor_data     TEXT,
                    acknowledged    INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_alerts_location_ts
                    ON alerts(location_name, timestamp DESC);
            """)

            for fault in constants.FAULT_IMPACT_SEVERITY:
                conn.execute(
                    "INSERT OR IGNORE INTO faults (fault_name) VALUES (?)",
                    (fault,)
                )

            for location in constants.LOCATIONS:
                conn.execute(
                    "INSERT OR IGNORE INTO locations (location_name) VALUES (?)",
                    (location,)
                )

            conn.commit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _location_id(self, conn: sqlite3.Connection, location_name: str) -> int:
        """Resolve a location name to its integer PK. Raises if the name is unknown.
        Callers pass the same connection so the lookup runs in the same transaction."""
        row = conn.execute(
            "SELECT id FROM locations WHERE location_name = ?", (location_name,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Unknown location: {location_name!r}")
        return row["id"]

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------
    def insert_data(self, data_row: dict, location_name: str, timestamp: str) -> int:
        """Insert a sensor reading. Returns the new row id."""
        with self._connect() as conn:
            loc_id = self._location_id(conn, location_name)
            cur = conn.execute(
                "INSERT INTO generated_data (location_id, data, timestamp) VALUES (?, ?, ?)",
                (loc_id, json.dumps(data_row), timestamp),
            )
            conn.commit()
            return cur.lastrowid

    def insert_label(
        self,
        data_row_id: int,
        if_label: int,
        rf_classification,
    ):
        """Attach anomaly labels to a data row."""
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO anomaly_labels
                   (data_row_id, isolation_forest_label, random_forest_classification)
                   VALUES (?, ?, ?)""",
                (data_row_id, int(if_label), json.dumps(rf_classification)),
            )
            conn.commit()

    def insert_fault(self, fault_name: str, location_name: str):
        with self._connect() as conn:
            fault_row = conn.execute(
                "SELECT id FROM faults WHERE fault_name = ?", (fault_name,)
            ).fetchone()
            if fault_row is None:
                raise ValueError(f"Unknown fault: {fault_name!r}")
            loc_id = self._location_id(conn, location_name)
            conn.execute(
                "UPDATE locations SET current_fault_id = ? WHERE id = ?",
                (fault_row["id"], loc_id),
            )
            conn.commit()

    def clear_faults(self):
        with self._connect() as conn:
            conn.execute("UPDATE locations SET current_fault_id = NULL")
            conn.commit()

    def clear_fault_for_location(self, location_name: str):
        with self._connect() as conn:
            loc_id = self._location_id(conn, location_name)
            conn.execute(
                "UPDATE locations SET current_fault_id = NULL WHERE id = ?",
                (loc_id,),
            )
            conn.commit()

    def clear_data(self):
        with self._connect() as conn:
            conn.executescript(
                "DELETE FROM anomaly_labels; DELETE FROM generated_data;"
            )
            conn.commit()

    def clear_alerts(self):
        with self._connect() as conn:
            conn.execute("DELETE FROM alerts")
            conn.commit()

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------
    def get_active_fault(self, location_name: str):
        """Return fault name string or None."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT f.fault_name
                   FROM locations l
                   LEFT JOIN faults f ON f.id = l.current_fault_id
                   WHERE l.location_name = ?""",
                (location_name,),
            ).fetchone()
            return row["fault_name"] if row else None

    def get_all_location_states(self) -> dict:
        """Return {location: {is_anomalous, active_fault}} for all locations.
        The correlated sub-select grabs only the most-recent data row per location
        so the JOIN doesn't pull every row into memory."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT l.location_name,
                          f.fault_name AS active_fault,
                          al.isolation_forest_label
                   FROM locations l
                   LEFT JOIN faults f ON f.id = l.current_fault_id
                   LEFT JOIN generated_data gd ON gd.location_id = l.id
                       AND gd.id = (
                           SELECT MAX(id) FROM generated_data
                           WHERE location_id = l.id
                       )
                   LEFT JOIN anomaly_labels al ON al.data_row_id = gd.id"""
            ).fetchall()
        states = {}
        for r in rows:
            states[r["location_name"]] = {
                "active_fault": r["active_fault"],
                "is_anomalous": r["isolation_forest_label"] == -1,
            }
        return states

    def get_latest_reading(self, location_name: str) -> dict:
        """Return the most recent sensor reading dict for a location."""
        with self._connect() as conn:
            loc_id = self._location_id(conn, location_name)
            row = conn.execute(
                """SELECT gd.data, gd.timestamp, al.isolation_forest_label,
                          al.random_forest_classification
                   FROM generated_data gd
                   LEFT JOIN anomaly_labels al ON al.data_row_id = gd.id
                   WHERE gd.location_id = ?
                   ORDER BY gd.timestamp DESC LIMIT 1""",
                (loc_id,),
            ).fetchone()
        if row is None:
            return {}
        return {
            "data": json.loads(row["data"]),
            "timestamp": row["timestamp"],
            "if_label": row["isolation_forest_label"],
            "rf_classification": json.loads(row["random_forest_classification"] or "null"),
        }

    def get_history(self, location_name: str, parameter: str, n: int = 50) -> list:
        """Return last n [timestamp, value] pairs for a parameter at a location."""
        with self._connect() as conn:
            loc_id = self._location_id(conn, location_name)
            rows = conn.execute(
                """SELECT timestamp, data FROM generated_data
                   WHERE location_id = ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (loc_id, n),
            ).fetchall()
        results = []
        for r in reversed(rows):
            try:
                d = json.loads(r["data"])
                if parameter in d:
                    results.append({"timestamp": r["timestamp"], "value": d[parameter]})
            except Exception:
                continue
        return results

    def get_recent_readings(self, location_name: str, n: int = 20) -> list:
        """Return last n full sensor readings with labels for a location."""
        with self._connect() as conn:
            loc_id = self._location_id(conn, location_name)
            rows = conn.execute(
                """SELECT gd.id, gd.data, gd.timestamp,
                          al.isolation_forest_label,
                          al.random_forest_classification
                   FROM generated_data gd
                   LEFT JOIN anomaly_labels al ON al.data_row_id = gd.id
                   WHERE gd.location_id = ?
                   ORDER BY gd.timestamp DESC LIMIT ?""",
                (loc_id, n),
            ).fetchall()
        results = []
        for r in reversed(rows):
            results.append({
                "id": r["id"],
                "data": json.loads(r["data"]),
                "timestamp": r["timestamp"],
                "if_label": r["isolation_forest_label"],
                "rf_classification": json.loads(r["random_forest_classification"] or "null"),
            })
        return results

    def get_row_count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM generated_data").fetchone()[0]

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    def insert_alert(
        self,
        location_name: str,
        timestamp: str,
        severity: str,
        fault_type: str | None,
        top_probability: float | None,
        sensor_data: dict | None,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO alerts
                   (location_name, timestamp, severity, fault_type,
                    top_probability, sensor_data)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    location_name, timestamp, severity, fault_type,
                    top_probability,
                    json.dumps(sensor_data) if sensor_data else None,
                ),
            )
            conn.commit()
            return cur.lastrowid

    def get_alerts(self, location_name: str = None, limit: int = 100,
                   unacked_only: bool = False) -> list:
        """Build a dynamic WHERE clause from optional filters then execute.
        params list is built in the same order as the placeholder ?s."""
        with self._connect() as conn:
            where_clauses = []
            params = []
            if location_name:
                where_clauses.append("location_name = ?")
                params.append(location_name)
            if unacked_only:
                where_clauses.append("acknowledged = 0")
            where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
            params.append(limit)
            rows = conn.execute(
                f"""SELECT id, location_name, timestamp, severity,
                           fault_type, top_probability, sensor_data, acknowledged
                    FROM alerts {where}
                    ORDER BY timestamp DESC LIMIT ?""",
                params,
            ).fetchall()
        results = []
        for r in rows:
            results.append({
                "id":              r["id"],
                "location":        r["location_name"],
                "timestamp":       r["timestamp"],
                "severity":        r["severity"],
                "fault_type":      r["fault_type"],
                "top_probability": r["top_probability"],
                "sensor_data":     json.loads(r["sensor_data"] or "null"),
                "acknowledged":    bool(r["acknowledged"]),
            })
        return results

    def acknowledge_alert(self, alert_id: int):
        with self._connect() as conn:
            conn.execute("UPDATE alerts SET acknowledged = 1 WHERE id = ?", (alert_id,))
            conn.commit()

    def acknowledge_all_alerts(self):
        with self._connect() as conn:
            conn.execute("UPDATE alerts SET acknowledged = 1")
            conn.commit()

    def get_alert_count(self, unacked_only: bool = True) -> int:
        with self._connect() as conn:
            clause = "WHERE acknowledged = 0" if unacked_only else ""
            return conn.execute(
                f"SELECT COUNT(*) FROM alerts {clause}"
            ).fetchone()[0]
