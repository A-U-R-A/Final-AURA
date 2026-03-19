# AURA Database — Entity Relationship Diagram

**Database engine:** SQLite 3 (WAL journal mode, foreign keys enforced)
**File path:** `data/aura.db`
**Schema managed by:** `database.py → Database._init_schema()`

---

## Entity Relationship Diagram

```
┌─────────────────────────────────┐
│             faults              │
├─────────────────────────────────┤
│ PK  id          INTEGER  AUTO   │
│     fault_name  TEXT     UNIQUE │
└──────────────┬──────────────────┘
               │  1
               │  Referenced by locations.current_fault_id
               │  (NULL when no fault active)
               │ 0..1
┌──────────────▼──────────────────┐
│            locations            │
├─────────────────────────────────┤
│ PK  id               INTEGER AUTO│
│     location_name    TEXT UNIQUE │
│ FK  current_fault_id INTEGER NULL│◄── NULL = nominal / no fault
└──────────────┬──────────────────┘
               │  1
               │  One location has many sensor readings
               │  M
┌──────────────▼──────────────────┐
│          generated_data         │
├─────────────────────────────────┤
│ PK  id           INTEGER AUTO   │
│ FK  location_id  INTEGER NOT NULL│
│     data         TEXT    NOT NULL│  ← JSON blob: {param: value, ...}
│     timestamp    TEXT    NOT NULL│  ← ISO-8601 string
└──────────────┬──────────────────┘
               │  1
               │  Each data row has exactly one label row (1:1)
               │  1
┌──────────────▼──────────────────┐
│         anomaly_labels          │
├─────────────────────────────────┤
│ PK  id                      INTEGER AUTO  │
│ FK  data_row_id              INTEGER UNIQUE NOT NULL │
│     isolation_forest_label   INTEGER NOT NULL        │ ← -1=anomalous, 1=normal
│     random_forest_classification TEXT               │ ← JSON: {fault: prob, ...}
└─────────────────────────────────┘


┌─────────────────────────────────────────────────────┐
│                      alerts                         │
├─────────────────────────────────────────────────────┤
│ PK  id               INTEGER  AUTO                  │
│     location_name    TEXT     NOT NULL               │ ← denormalised (no FK)
│     timestamp        TEXT     NOT NULL               │ ← ISO-8601 string
│     severity         TEXT     NOT NULL  DEFAULT 'WARNING' │ ← 'WARNING'|'CRITICAL'
│     fault_type       TEXT     NULL                   │ ← top RF prediction label
│     top_probability  REAL     NULL                   │ ← 0.0–1.0
│     sensor_data      TEXT     NULL                   │ ← JSON snapshot at alert time
│     acknowledged     INTEGER  NOT NULL  DEFAULT 0    │ ← 0=unread, 1=acked
└─────────────────────────────────────────────────────┘
```

---

## Table Reference

### `faults`
Lookup / seed table. Populated once at startup from `constants.FAULT_IMPACT_SEVERITY`.
Never written to at runtime — acts as a read-only reference.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | Surrogate key |
| `fault_name` | TEXT | NOT NULL, UNIQUE | Human-readable fault name. One of: `Cabin Leak`, `O2 Generator Failure`, `O2 Leak`, `CO2 Scrubber Failure`, `CHX Failure`, `Water Processor Failure`, `Trace Contaminant Filter Saturation`, `NH3 Coolant Leak` |

**Seeded values (8 rows, static):**
```
1  Cabin Leak
2  O2 Generator Failure
3  O2 Leak
4  CO2 Scrubber Failure
5  CHX Failure
6  Water Processor Failure
7  Trace Contaminant Filter Saturation
8  NH3 Coolant Leak
```

---

### `locations`
Lookup / seed table + mutable fault state. Populated once at startup from `constants.LOCATIONS`.
`current_fault_id` is the only column written to at runtime — tracks which fault (if any) is currently injected at each ISS module.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | Surrogate key |
| `location_name` | TEXT | NOT NULL, UNIQUE | ISS module name. One of: `JLP & JPM`, `Node 2`, `Columbus`, `US Lab`, `Cupola`, `Node 1`, `Joint Airlock` |
| `current_fault_id` | INTEGER | NULL, FK → `faults(id)` | Currently active injected fault. **NULL = nominal state.** Set via `POST /api/faults/inject`. Cleared by `DELETE /api/faults` or `DELETE /api/faults/{location}`. |

**Seeded values (7 rows, `current_fault_id` mutable):**
```
1  JLP & JPM       NULL
2  Node 2          NULL
3  Columbus        NULL
4  US Lab          NULL
5  Cupola          NULL
6  Node 1          NULL
7  Joint Airlock   NULL
```

**Business rules:**
- Each location can have at most ONE active fault at a time (FK to single row in `faults`)
- Setting a new fault on a location replaces the previous one (UPDATE, not INSERT)
- Clearing faults sets `current_fault_id = NULL` — does not delete the row
- The data generator reads this column every tick to determine drift direction

---

### `generated_data`
Core time-series table. One row per sensor tick per location (~1 row/second × 7 locations = ~7 rows/second at runtime). High write volume; pruned manually via `DELETE /api/data`. WAL mode is critical for concurrent reads during writes.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | Surrogate key; monotonically increasing |
| `location_id` | INTEGER | NOT NULL, FK → `locations(id)` | Which ISS module this reading is from |
| `data` | TEXT | NOT NULL | **JSON blob** containing all 20 sensor parameter values. See JSON schema below. |
| `timestamp` | TEXT | NOT NULL | ISO-8601 datetime string (e.g. `2026-03-17T14:23:01.123456`) generated by the server at tick time |

**Index:** `idx_data_location_ts ON generated_data(location_id, timestamp DESC)`
Used by: `get_latest_reading()`, `get_history()`, `get_recent_readings()`, `get_all_location_states()`

**`data` JSON schema (20 parameters):**
```json
{
  "O2 partial pressure":                    21.3,
  "CO2 partial pressure":                   0.42,
  "Humidity":                               0.52,
  "O2 output rate (generator)":             5.2,
  "O2 purity (generator)":                  0.997,
  "Water purity":                           1.4,
  "Production rate (water recovery system)": 32.1,
  "Temperature":                            22.4,
  "NH3":                                    0.3,
  "H2 (%)":                                 0.02,
  "CO":                                     2.1,
  "Airflow rate":                           0.6,
  "Cabin pressure":                         14.7,
  "Bacterial/fungal count":                 12.0,
  "N2":                                     0.77,
  "O2":                                     21.1,
  "CO2":                                    0.41,
  "CH4":                                    3.2,
  "H2 (ppm)":                               4.1,
  "H2O":                                    0.41
}
```

**Growth rate:** ~7 rows/second → ~25,200 rows/hour → ~604,800 rows/day (unbounded; clear via `DELETE /api/data`)

---

### `anomaly_labels`
One-to-one extension of `generated_data`. Stores ML pipeline output for each sensor reading. Written immediately after `generated_data` insert, within the same tick. The UNIQUE constraint on `data_row_id` enforces the 1:1 relationship.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | Surrogate key |
| `data_row_id` | INTEGER | NOT NULL, UNIQUE, FK → `generated_data(id)` | The sensor reading this label belongs to. UNIQUE enforces 1:1. |
| `isolation_forest_label` | INTEGER | NOT NULL | Isolation Forest output: **-1 = anomalous**, **1 = normal** |
| `random_forest_classification` | TEXT | NULL | **JSON blob**: probability distribution over all 8 fault classes. NULL when IF label = 1 (normal). See schema below. |

**`random_forest_classification` JSON schema:**
```json
{
  "CHX Failure":                          0.014,
  "CO2 Scrubber Failure":                 0.312,
  "Cabin Leak":                           0.060,
  "NH3 Coolant Leak":                     0.065,
  "O2 Generator Failure":                 0.073,
  "O2 Leak":                              0.429,
  "Trace Contaminant Filter Saturation":  0.031,
  "Water Processor Failure":              0.016
}
```
All values sum to 1.0. The key with the highest value is the top predicted fault class.

**Note:** `INSERT OR IGNORE` is used — if a label already exists for a `data_row_id`, the insert is silently skipped (prevents duplicate labels on server restart overlap).

---

### `alerts`
Event log for anomaly alerts fired by the alert debounce system. Intentionally **denormalized** — `location_name` is stored as text rather than a foreign key to preserve alert history even if location data is cleared. A snapshot of sensor values at alert time is embedded in `sensor_data`.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `id` | INTEGER | PK, AUTOINCREMENT | Surrogate key |
| `location_name` | TEXT | NOT NULL | ISS module name (denormalized — no FK to `locations`) |
| `timestamp` | TEXT | NOT NULL | ISO-8601 datetime of when the alert fired |
| `severity` | TEXT | NOT NULL, DEFAULT `'WARNING'` | `'WARNING'` when top RF probability ≤ 0.70; `'CRITICAL'` when > 0.70 |
| `fault_type` | TEXT | NULL | Top predicted fault class from RF at alert time (e.g. `"O2 Leak"`). NULL if RF was not run (no anomaly label). |
| `top_probability` | REAL | NULL | RF probability for `fault_type`, 0.0–1.0. NULL if `fault_type` is NULL. |
| `sensor_data` | TEXT | NULL | **JSON blob**: full sensor reading snapshot at time of alert (same schema as `generated_data.data`). Allows post-hoc forensic review without joining to `generated_data`. |
| `acknowledged` | INTEGER | NOT NULL, DEFAULT `0` | **0 = unread/active**, **1 = acknowledged by operator**. Updated via `PATCH /api/alerts/{id}/acknowledge` or `POST /api/alerts/acknowledge-all`. |

**Index:** `idx_alerts_location_ts ON alerts(location_name, timestamp DESC)`
Used by: `get_alerts()` filtered by location and timestamp ordering.

**Alert firing conditions (defined in `main.py`):**
- `ALERT_MIN_CONSECUTIVE = 5` — IF must label 5 consecutive ticks as anomalous before alert fires
- `ALERT_COOLDOWN_SECONDS = 300` — minimum 5 minutes between repeat alerts for the same location
- Alerts are suppressed when `fault_type` matches the currently active injected fault (known injection)

---

## Relationships Summary

```
faults          1 ──── 0..N    locations           (via current_fault_id FK)
locations       1 ──── 0..N    generated_data      (via location_id FK)
generated_data  1 ──── 0..1    anomaly_labels      (via data_row_id FK, UNIQUE)
alerts          (standalone — location_name is denormalized TEXT, no FK)
```

| Relationship | Cardinality | Join Column | Notes |
|---|---|---|---|
| `faults` → `locations` | 1 : 0..N | `locations.current_fault_id` = `faults.id` | One fault type can be active at multiple locations simultaneously |
| `locations` → `generated_data` | 1 : 0..N | `generated_data.location_id` = `locations.id` | Unbounded time-series; all 7 locations produce rows every tick |
| `generated_data` → `anomaly_labels` | 1 : 0..1 | `anomaly_labels.data_row_id` = `generated_data.id` | UNIQUE constraint enforces 1:1; label may be absent if ML pipeline disabled |
| `alerts` (none) | standalone | `location_name` TEXT | Denormalized to survive `DELETE FROM generated_data` without orphaning alert history |

---

## Indexes

| Index Name | Table | Columns | Purpose |
|---|---|---|---|
| `idx_data_location_ts` | `generated_data` | `(location_id, timestamp DESC)` | Fast retrieval of latest/recent readings per location — used on every tick |
| `idx_alerts_location_ts` | `alerts` | `(location_name, timestamp DESC)` | Fast alert queries filtered by location with newest-first ordering |

---

## Data Flow (Write Path)

```
Every tick (~1s per location):

  1. SensorDataGenerator.sample()
        └─► database.insert_data()
                └─► INSERT INTO generated_data → returns row_id

  2. MLPipeline.predict(reading)
        ├─► IsolationForest.predict()  → if_label (-1 or 1)
        └─► RandomForest.predict_proba() → rf_classification JSON

  3. database.insert_label(row_id, if_label, rf_classification)
        └─► INSERT OR IGNORE INTO anomaly_labels

  4. Alert debounce check (if if_label == -1):
        └─► database.insert_alert()  [if 5 consecutive + 5min cooldown met]
                └─► INSERT INTO alerts
```

## Data Flow (Read Path)

```
GET /api/location/{loc}/latest
  └─► database.get_latest_reading()
        └─► SELECT generated_data + anomaly_labels (LEFT JOIN, ORDER BY timestamp DESC LIMIT 1)

GET /api/location/{loc}/history/{param}
  └─► database.get_history()
        └─► SELECT generated_data WHERE location_id = ? ORDER BY timestamp DESC LIMIT n
            (extracts single parameter value from JSON blob in application layer)

GET /api/alerts
  └─► database.get_alerts()
        └─► SELECT alerts WHERE [location] [acknowledged] ORDER BY timestamp DESC LIMIT n

GET /api/subsystems (location states)
  └─► database.get_all_location_states()
        └─► SELECT locations LEFT JOIN faults LEFT JOIN generated_data LEFT JOIN anomaly_labels
            (correlated subquery to find latest generated_data row per location)
```

---

## Operational Notes

### WAL Mode
`PRAGMA journal_mode=WAL` is set on every connection open. This allows concurrent readers during the high-frequency write loop (7 inserts/second) without blocking the FastAPI HTTP handlers.

### Foreign Key Enforcement
`PRAGMA foreign_keys=ON` is set per connection. SQLite does not enforce FKs by default — this pragma must be set each time a connection is opened (it is not persisted).

### JSON Columns
`generated_data.data`, `anomaly_labels.random_forest_classification`, and `alerts.sensor_data` are all TEXT columns storing JSON. Serialization/deserialization is handled entirely in `database.py` using `json.dumps()` / `json.loads()`. SQLite's JSON functions (`json_extract`) are not used — parsing happens in Python.

### Data Lifecycle
| Table | Cleared by | Notes |
|---|---|---|
| `generated_data` | `DELETE /api/data` | Also clears `anomaly_labels` via cascade delete in app layer |
| `anomaly_labels` | `DELETE /api/data` | Cleared together with `generated_data` |
| `alerts` | `DELETE /api/data` | Cleared alongside sensor data in updated `clear_data()` |
| `locations.current_fault_id` | `DELETE /api/faults` | Sets to NULL; does not delete location rows |
| `faults` | Never cleared | Static reference data; re-seeded on startup if empty |
| `locations` | Never cleared | Static reference data; re-seeded on startup if empty |

### Denormalization Decision (`alerts.location_name`)
`alerts` stores `location_name` as TEXT rather than a FK to `locations.id`. This is intentional: alerts must survive `DELETE FROM generated_data` and remain readable as a historical audit log even after sensor data is purged. A FK would risk orphaned alert rows or require cascading deletes that would destroy audit history.
