import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import constants
from database import Database
from data_generator import SensorDataGenerator
from ml_pipeline import MLPipeline
from lstm_predictor import LSTMPipeline
from dqn_recommender import DQNRecommender
import ai_analyst
import trend_detector

# ---------------------------------------------------------------------------
# Shared application state
# ---------------------------------------------------------------------------

db = Database()
generator = SensorDataGenerator(seed=42, step_seconds=60.0)
ml = MLPipeline()
lstm = LSTMPipeline()
dqn = DQNRecommender()

# Connected WebSocket clients
_ws_clients: Set[WebSocket] = set()

# Per-location alert state.
# Strategy: require N *consecutive* anomalous readings before firing an alert,
# then enforce a cooldown before the next one.  This filters out isolated
# false-positives (random single-tick IF misclassifications) while still
# catching real sustained faults quickly.
# N is determined per-fault from constants.FAULT_ALERT_CONFIG.
_alert_last_ts: dict[str, datetime] = {}
_alert_consec:  dict[str, int]      = {}   # consecutive anomalous tick count
ALERT_COOLDOWN_SECONDS = 300  # min seconds between repeat alerts for same location/fault (legacy; use per-fault config)

# Fault latch: once RF confidence >= LATCH_THRESHOLD the detected fault is pinned
# for that location and will keep showing even on nominal IF ticks, until the user
# explicitly resolves it via DELETE /api/faults/latch/{location}.
_latched_fault:  dict[str, str] = {}
_latch_alerted:  set[str]       = set()   # locations that have had their latch alert fired
LATCH_THRESHOLD = 0.90


async def _broadcast(message: dict):
    """Send a JSON message to all connected WebSocket clients."""
    if not _ws_clients:
        return
    data = json.dumps(message, default=str)
    dead = set()
    for ws in _ws_clients:
        try:
            await ws.send_text(data)
        except Exception:
            dead.add(ws)
    _ws_clients.difference_update(dead)


# ---------------------------------------------------------------------------
# Background data-generation loop
# ---------------------------------------------------------------------------

async def _generation_loop():
    """
    Every DATA_GENERATION_INTERVAL seconds, sample all ISS locations,
    run ML inference, persist to DB, and broadcast via WebSocket.
    """
    while True:
        try:
            ts = datetime.now().isoformat()
            location_states = {}

            for location in constants.LOCATIONS:
                active_fault = db.get_active_fault(location)
                reading = generator.sample(location, active_fault)

                row_id = db.insert_data(reading, location, ts)
                if_label, rf_class = ml.predict(reading)
                db.insert_label(row_id, if_label, rf_class)

                # Feed LSTM rolling buffer
                lstm.push(location, reading)
                lstm_pred = lstm.predict(location)

                # DQN action recommendation
                anomaly_score = ml.anomaly_score(reading) if ml.enabled else 0.0
                if lstm_pred:
                    failure_prob = lstm_pred["failure_prob"]
                    rul_hours    = lstm_pred["rul_hours"]
                elif if_label == -1 and rf_class:
                    # LSTM buffer still filling (needs seq_len readings).
                    # Use RF top-class confidence as failure_prob proxy so the
                    # DQN gets the fault signal immediately instead of seeing
                    # failure_prob=0 and defaulting to "No Action Needed".
                    top_fault, top_prob = max(rf_class.items(), key=lambda x: x[1])
                    failure_prob = float(top_prob)
                    rul_hours    = constants.FAULT_PRECURSOR_HOURS.get(top_fault, 12.0) * 0.5
                else:
                    failure_prob = 0.0
                    rul_hours    = 200.0
                dqn_rec = dqn.recommend(
                    sensor_data=reading,
                    anomaly_score=anomaly_score,
                    if_label=if_label,
                    rf_classification=rf_class,
                    failure_prob=failure_prob,
                    rul_hours=rul_hours,
                )

                # Alert debounce: increment consecutive counter on anomaly,
                # reset to 0 on any nominal reading.  Only fire when the
                # counter hits the fault-specific threshold AND cooldown has elapsed.
                # Thresholds per fault are in constants.FAULT_ALERT_CONFIG.
                is_anomalous = if_label == -1
                if is_anomalous:
                    _alert_consec[location] = _alert_consec.get(location, 0) + 1
                else:
                    _alert_consec[location] = 0

                consec     = _alert_consec.get(location, 0)
                now_dt     = datetime.fromisoformat(ts)
                # Cooldown key is per-location so bursts at one location don't
                # block alerts at another.
                last_ts    = _alert_last_ts.get(location)
                
                # Determine alert threshold based on detected fault type
                top_fault, top_prob = None, None
                if rf_class:
                    top_fault, top_prob = max(rf_class.items(), key=lambda x: x[1])
                
                # Get per-fault config; fall back to sensible defaults
                fault_config = constants.FAULT_ALERT_CONFIG.get(top_fault, {})
                min_consecutive = fault_config.get("min_consecutive", 30)  # default 30 ticks
                cooldown_seconds = fault_config.get("cooldown_seconds", 300)  # default 5 min
                
                cooldown_ok = (
                    last_ts is None or
                    (now_dt - last_ts).total_seconds() >= cooldown_seconds
                )

                if consec >= min_consecutive and cooldown_ok:
                    severity = "CRITICAL" if (top_prob or 0) > 0.7 else "WARNING"
                    db.insert_alert(
                        location_name=location,
                        timestamp=ts,
                        severity=severity,
                        fault_type=top_fault,
                        top_probability=top_prob,
                        sensor_data=reading,
                    )
                    await _broadcast({
                        "type":       "alert",
                        "location":   location,
                        "severity":   severity,
                        "fault_type": top_fault,
                        "top_prob":   top_prob,
                        "timestamp":  ts,
                    })
                    _alert_last_ts[location] = now_dt
                    _alert_consec[location]  = 0   # reset so next burst is fresh

                # detected_fault: what the AI has actually identified.
                # The injected fault (active_fault) is kept internal — it only
                # drives sensor drift in the generator.  The frontend only learns
                # about a fault once IF flags anomalous AND RF classifies it with
                # >= 60 % confidence.  This keeps visuals 100 % AI-driven.
                # Once RF confidence hits LATCH_THRESHOLD (90%), the fault display
                # is pinned for that location and will not bounce back to nominal
                # until the user explicitly resolves it.
                detected_fault = None
                if if_label == -1 and rf_class:
                    _top_fault, _top_prob = max(rf_class.items(), key=lambda x: x[1])
                    if _top_prob >= 0.60:
                        detected_fault = _top_fault
                    if _top_prob >= LATCH_THRESHOLD:
                        newly_latched = location not in _latched_fault
                        _latched_fault[location] = _top_fault
                        if newly_latched and location not in _latch_alerted:
                            _latch_alerted.add(location)
                            db.insert_alert(
                                location_name=location,
                                timestamp=ts,
                                severity="CRITICAL",
                                fault_type=_top_fault,
                                top_probability=_top_prob,
                                sensor_data=reading,
                            )
                            await _broadcast({
                                "type":       "alert",
                                "location":   location,
                                "severity":   "CRITICAL",
                                "fault_type": _top_fault,
                                "top_prob":   _top_prob,
                                "timestamp":  ts,
                                "latched":    True,
                            })

                # Honor the latch even when IF returns nominal
                if detected_fault is None and location in _latched_fault:
                    detected_fault = _latched_fault[location]

                location_states[location] = {
                    "active_fault": detected_fault,
                    "is_anomalous": if_label == -1,
                    "latched": location in _latched_fault,
                }

                tick_msg = {
                    "type": "tick",
                    "location": location,
                    "timestamp": ts,
                    "data": reading,
                    "if_label": if_label,
                    "rf_classification": rf_class,
                    "active_fault": detected_fault,
                }
                if lstm_pred:
                    tick_msg["lstm"] = lstm_pred
                if dqn.enabled:
                    tick_msg["dqn"] = {
                        "action":       dqn_rec["action"],
                        "action_index": dqn_rec["action_index"],
                        "confidence":   round(dqn_rec["confidence"], 4),
                    }
                await _broadcast(tick_msg)

            # Send a consolidated location-state summary after all ticks
            await _broadcast({
                "type": "state",
                "locations": location_states,
                "timestamp": ts,
            })

        except Exception as e:
            print(f"[loop] Error: {e}")

        await asyncio.sleep(constants.DATA_GENERATION_INTERVAL)


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_generation_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="AURA — ECLSS AI Predictive Maintenance",
    version="2.0.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# REST API routes
# ---------------------------------------------------------------------------

@app.get("/api/config")
def get_config():
    """Return all static configuration the frontend needs."""
    return {
        "locations": constants.LOCATIONS,
        "location_positions": constants.LOCATION_POSITIONS,
        "subsystem_parameters": constants.SUBSYSTEM_PARAMETERS,
        "parameter_nominal_ranges": constants.PARAMETER_NOMINAL_RANGES,
        "parameter_units": constants.PARAMETER_UNITS,
        "faults": list(constants.FAULT_IMPACT_SEVERITY.keys()),
        "actions": constants.ACTIONS_TO_TAKE,
        "fault_precursor_hours": constants.FAULT_PRECURSOR_HOURS,
        "ml_enabled":   ml.enabled,
        "lstm_enabled": lstm.enabled,
        "dqn_enabled":  dqn.enabled,
    }


@app.get("/api/locations")
def get_locations():
    """All locations with their current anomaly state."""
    return db.get_all_location_states()


@app.get("/api/location/{location_name}/latest")
def get_latest(location_name: str):
    """Most recent sensor reading + labels for a location."""
    if location_name not in constants.LOCATIONS:
        raise HTTPException(404, f"Location {location_name!r} not found")
    return db.get_latest_reading(location_name)


@app.get("/api/location/{location_name}/history/{parameter}")
def get_history(location_name: str, parameter: str, n: int = 50):
    """Last n readings for a specific parameter at a location."""
    if location_name not in constants.LOCATIONS:
        raise HTTPException(404, f"Location {location_name!r} not found")
    return db.get_history(location_name, parameter, n)


@app.get("/api/location/{location_name}/readings")
def get_readings(location_name: str, n: int = 20):
    """Last n full sensor readings with labels for a location."""
    if location_name not in constants.LOCATIONS:
        raise HTTPException(404, f"Location {location_name!r} not found")
    return db.get_recent_readings(location_name, n)


@app.get("/api/subsystems")
def get_subsystems():
    """Current values for all subsystem parameters across all locations."""
    results = {}
    for subsystem, params in constants.SUBSYSTEM_PARAMETERS.items():
        results[subsystem] = {}
        for param in params:
            # Use first location that has data
            for location in constants.LOCATIONS:
                reading = db.get_latest_reading(location)
                if reading and "data" in reading and param in reading["data"]:
                    results[subsystem][param] = {
                        "value": reading["data"][param],
                        "location": location,
                        "unit": constants.PARAMETER_UNITS.get(param, ""),
                        "nominal_range": constants.PARAMETER_NOMINAL_RANGES.get(param),
                    }
                    break
            else:
                results[subsystem][param] = {
                    "value": None,
                    "location": None,
                    "unit": constants.PARAMETER_UNITS.get(param, ""),
                    "nominal_range": constants.PARAMETER_NOMINAL_RANGES.get(param),
                }
    return results


# ---------------------------------------------------------------------------
# Fault control
# ---------------------------------------------------------------------------

class FaultRequest(BaseModel):
    location: str
    fault: str


@app.post("/api/faults/inject")
def inject_fault(req: FaultRequest):
    if req.location not in constants.LOCATIONS:
        raise HTTPException(404, f"Location {req.location!r} not found")
    if req.fault not in constants.FAULT_IMPACT_SEVERITY:
        raise HTTPException(400, f"Unknown fault {req.fault!r}")
    db.insert_fault(req.fault, req.location)
    # Clear LSTM buffer so old nominal readings don't dilute the fault signal.
    # The buffer refills with fault-drifted data within seq_len real seconds.
    lstm.clear_buffer(req.location)
    return {"status": "ok", "location": req.location, "fault": req.fault}


@app.delete("/api/faults")
def clear_all_faults():
    db.clear_faults()
    generator.reset_drift()
    lstm.clear_buffer()
    _alert_consec.clear()
    _alert_last_ts.clear()
    _latched_fault.clear()
    _latch_alerted.clear()
    return {"status": "ok"}


@app.delete("/api/faults/{location_name}")
def clear_location_fault(location_name: str):
    if location_name not in constants.LOCATIONS:
        raise HTTPException(404, f"Location {location_name!r} not found")
    db.clear_fault_for_location(location_name)
    generator.reset_drift(location_name)
    lstm.clear_buffer(location_name)
    _alert_consec.pop(location_name, None)
    _alert_last_ts.pop(location_name, None)
    _latched_fault.pop(location_name, None)
    _latch_alerted.discard(location_name)
    return {"status": "ok", "location": location_name}


@app.delete("/api/faults/latch/{location_name}")
def resolve_latched_fault(location_name: str):
    """Resolve (un-latch) a pinned fault for a location without clearing the
    underlying fault injection or resetting drift.  Use this when the fault has
    been physically addressed and the display should return to AI-driven state."""
    if location_name not in constants.LOCATIONS:
        raise HTTPException(404, f"Location {location_name!r} not found")
    _latched_fault.pop(location_name, None)
    _latch_alerted.discard(location_name)
    return {"status": "ok", "location": location_name}


@app.delete("/api/data")
def clear_data():
    db.clear_data()
    db.clear_alerts()
    generator.reset_drift()
    generator._mission_seconds = 0.0
    # Also reset alert debounce state so stale consecutive counts don't linger
    _alert_consec.clear()
    _alert_last_ts.clear()
    return {"status": "ok", "message": "All sensor data, alerts, and drift state cleared"}


# ---------------------------------------------------------------------------
# Prediction (LSTM)
# ---------------------------------------------------------------------------

@app.get("/api/location/{location_name}/prediction")
def get_prediction(location_name: str):
    """LSTM failure probability + RUL estimate for a location."""
    if location_name not in constants.LOCATIONS:
        raise HTTPException(404, f"Location {location_name!r} not found")
    pred = lstm.predict(location_name)
    if pred is None:
        buffer_len = len(lstm._buffers.get(location_name, []))
        return {
            "location": location_name,
            "lstm_enabled": lstm.enabled,
            "ready": False,
            "buffer_fill": buffer_len,
            "seq_len": lstm.seq_len,
            "failure_prob": None,
            "rul_hours": None,
        }
    return {
        "location": location_name,
        "lstm_enabled": True,
        "ready": True,
        "failure_prob": round(pred["failure_prob"], 4),
        "rul_hours": round(pred["rul_hours"], 2),
    }


# ---------------------------------------------------------------------------
# DQN Action Recommendation
# ---------------------------------------------------------------------------

@app.get("/api/location/{location_name}/recommendation")
def get_recommendation(location_name: str):
    """DQN-recommended remediation action for the current system state."""
    if location_name not in constants.LOCATIONS:
        raise HTTPException(404, f"Location {location_name!r} not found")

    latest = db.get_latest_reading(location_name)
    if not latest or not latest.get("data"):
        return {
            "location":    location_name,
            "dqn_enabled": dqn.enabled,
            "ready":       False,
            "action":      "No Action Needed",
            "action_index": 0,
            "confidence":  0.0,
            "q_values":    None,
        }

    sensor_data = latest["data"]
    if_label    = latest.get("if_label") or 1
    rf_class    = latest.get("rf_classification")

    anomaly_score = ml.anomaly_score(sensor_data) if ml.enabled else 0.0
    lstm_pred     = lstm.predict(location_name)
    failure_prob  = lstm_pred["failure_prob"] if lstm_pred else 0.0
    rul_hours     = lstm_pred["rul_hours"]    if lstm_pred else 200.0

    result = dqn.recommend(
        sensor_data=sensor_data,
        anomaly_score=anomaly_score,
        if_label=if_label,
        rf_classification=rf_class,
        failure_prob=failure_prob,
        rul_hours=rul_hours,
    )
    return {
        "location":    location_name,
        "dqn_enabled": dqn.enabled,
        "ready":       True,
        **result,
    }


# ---------------------------------------------------------------------------
# Maintenance schedule
# ---------------------------------------------------------------------------

@app.get("/api/maintenance")
def get_maintenance():
    """
    Returns estimated replacement schedule (based on MTBF) and calibration
    schedule (based on cumulative sensor drift) for all ECLSS subsystems.
    """
    elapsed_hours = generator.mission_elapsed_hours
    elapsed_weeks = elapsed_hours / 168.0  # 168 h/week

    # ── Replacement schedule ──────────────────────────────────────────────
    replacement = []
    for fault_name, mtbf in constants.SENSOR_MTBF_HOURS.items():
        remaining = max(0.0, mtbf - elapsed_hours)
        pct_used  = min(1.0, elapsed_hours / mtbf)
        if pct_used >= 0.90:
            status = "CRITICAL"
        elif pct_used >= 0.75:
            status = "WARNING"
        elif pct_used >= 0.50:
            status = "CAUTION"
        else:
            status = "NOMINAL"
        rec = constants.MAINTENANCE_RECOMMENDATIONS.get(fault_name, {})
        replacement.append({
            "subsystem":          fault_name,
            "subsystem_full":     rec.get("subsystem", fault_name),
            "maintenance_type":   rec.get("maintenance_type", "condition_based"),
            "primary_action":     rec.get("primary_action", "Inspect and service per maintenance manual."),
            "interval_note":      rec.get("interval_note", ""),
            "smac_trigger":       rec.get("smac_trigger", ""),
            "source":             rec.get("source", ""),
            "mtbf_hours":         mtbf,
            "elapsed_hours":      round(elapsed_hours, 1),
            "remaining_hours":    round(remaining, 1),
            "pct_life_used":      round(pct_used * 100, 1),
            "status":             status,
        })
    replacement.sort(key=lambda x: x["pct_life_used"], reverse=True)

    # ── Calibration schedule ──────────────────────────────────────────────
    # Flag for calibration when cumulative drift exceeds 2 % of nominal span
    CAL_THRESHOLD_FRACTION = 0.02
    calibration = []
    for param, drift_per_week in constants.CALIBRATION_DRIFT_PER_WEEK.items():
        if drift_per_week <= 0:
            continue
        cumulative = drift_per_week * elapsed_weeks
        weeks_to_threshold = CAL_THRESHOLD_FRACTION / drift_per_week
        weeks_remaining    = max(0.0, weeks_to_threshold - elapsed_weeks)
        cum_pct = min(100.0, round(cumulative / CAL_THRESHOLD_FRACTION * 100, 1))
        if weeks_remaining <= 0:
            status = "OVERDUE"
        elif weeks_remaining < 1:
            status = "DUE_SOON"
        elif weeks_remaining < 4:
            status = "CAUTION"
        else:
            status = "NOMINAL"
        calibration.append({
            "parameter":              param,
            "unit":                   constants.PARAMETER_UNITS.get(param, ""),
            "drift_per_week_pct":     round(drift_per_week * 100, 4),
            "cumulative_drift_pct":   cum_pct,
            "weeks_until_cal":        round(weeks_remaining, 1),
            "status":                 status,
        })
    calibration.sort(key=lambda x: x["weeks_until_cal"])

    return {
        "mission_elapsed_hours": round(elapsed_hours, 1),
        "mission_elapsed_days":  round(elapsed_hours / 24, 1),
        "replacement_schedule":  replacement,
        "calibration_schedule":  calibration,
    }


# ---------------------------------------------------------------------------
# Trend detection
# ---------------------------------------------------------------------------

@app.get("/api/location/{location_name}/trends")
def get_trends(location_name: str, n: int = 100):
    """Mann-Kendall + Sen's slope + CUSUM trend analysis for all parameters."""
    if location_name not in constants.LOCATIONS:
        raise HTTPException(404, f"Location {location_name!r} not found")

    all_params = list(constants.PARAMETER_NOMINAL_RANGES.keys())
    history = {}
    for param in all_params:
        rows = db.get_history(location_name, param, n)
        if rows:
            history[param] = [r["value"] for r in rows]

    results = trend_detector.analyze_location(location_name, history)
    return {"location": location_name, "n_readings": n, "trends": results}


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

@app.get("/api/alerts")
def get_alerts(location: str = None, limit: int = 100, unacked_only: bool = False):
    """Return alert history, optionally filtered by location."""
    return db.get_alerts(location_name=location, limit=limit, unacked_only=unacked_only)


@app.get("/api/alerts/count")
def get_alert_count():
    return {"unacknowledged": db.get_alert_count(unacked_only=True),
            "total": db.get_alert_count(unacked_only=False)}


@app.post("/api/alerts/{alert_id}/acknowledge")
def acknowledge_alert(alert_id: int):
    db.acknowledge_alert(alert_id)
    return {"status": "ok"}


@app.post("/api/alerts/acknowledge-all")
def acknowledge_all():
    db.acknowledge_all_alerts()
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# AI Analysis
# ---------------------------------------------------------------------------

class AnalysisRequest(BaseModel):
    location: str
    model: str = "mistral"


@app.post("/api/ai/analyze")
async def analyze(req: AnalysisRequest):
    if req.location not in constants.LOCATIONS:
        raise HTTPException(404, f"Location {req.location!r} not found")
    readings = db.get_data_for_prompt(req.location, n=10)
    # Run in thread pool so we don't block the event loop
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, ai_analyst.analyze, req.location, readings, req.model
    )
    return {"location": req.location, "response": result}


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    await websocket.accept()
    _ws_clients.add(websocket)

    # Send current state immediately on connect
    try:
        state = db.get_all_location_states()
        await websocket.send_text(json.dumps({
            "type": "state",
            "locations": state,
            "timestamp": datetime.now().isoformat(),
        }))
    except Exception:
        pass

    try:
        while True:
            # Keep connection alive; clients can send pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        _ws_clients.discard(websocket)
    except Exception:
        _ws_clients.discard(websocket)


# ---------------------------------------------------------------------------
# Static file serving (frontend)
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def root():
    return FileResponse("static/index.html")


@app.get("/health")
def health():
    return {
        "status": "nominal",
        "ml_enabled":   ml.enabled,
        "lstm_enabled": lstm.enabled,
        "dqn_enabled":  dqn.enabled,
        "db_rows": db.get_row_count(),
        "unacked_alerts": db.get_alert_count(unacked_only=True),
        "ws_clients": len(_ws_clients),
    }
