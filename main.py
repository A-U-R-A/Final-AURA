import asyncio
import json
import os
import time as _time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import constants
from app.database import Database
from app.data_generator import SensorDataGenerator
from app.ml_pipeline import MLPipeline
from app.lstm_predictor import LSTMPipeline
from app.dqn_recommender import DQNRecommender
from app import ai_analyst
from app import trend_detector
from app import settings_manager

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
_alert_last_ts: dict[str, datetime] = {}
_alert_consec:  dict[str, int]      = {}
_alert_top_fault: dict[str, str | None] = {}
ALERT_MIN_CONSECUTIVE   = settings_manager.get("alert_min_consecutive",  10)
ALERT_COOLDOWN_SECONDS  = settings_manager.get("alert_cooldown_seconds", 600)
ALERT_CRITICAL_RF_GATE  = settings_manager.get("alert_critical_rf_gate", 0.85)

# Fault latch
_latched_fault:  dict[str, str] = {}
_latch_alerted:  set[str]       = set()
_latch_consec:   dict[str, int] = {}
_latch_streak:   dict[str, str] = {}
LATCH_THRESHOLD       = settings_manager.get("latch_threshold",       0.95)
LATCH_MIN_CONSECUTIVE = settings_manager.get("latch_min_consecutive", 3)


async def _broadcast(message: dict):
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

                lstm.push(location, reading)
                lstm_pred = lstm.predict(location)

                anomaly_score = ml.anomaly_score(reading) if ml.enabled else 0.0
                if lstm_pred:
                    failure_prob = lstm_pred["failure_prob"]
                    rul_hours    = lstm_pred["rul_hours"]
                elif if_label == -1 and rf_class:
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

                is_anomalous = if_label == -1
                if is_anomalous:
                    tick_top_fault = None
                    if rf_class:
                        tick_top_fault, _ = max(rf_class.items(), key=lambda x: x[1])

                    prev_streak_fault = _alert_top_fault.get(location)
                    if prev_streak_fault is not None and tick_top_fault != prev_streak_fault:
                        _alert_consec[location] = 1
                    else:
                        _alert_consec[location] = _alert_consec.get(location, 0) + 1
                    _alert_top_fault[location] = tick_top_fault
                else:
                    _alert_consec[location]    = 0
                    _alert_top_fault[location] = None

                consec  = _alert_consec.get(location, 0)
                now_dt  = datetime.fromisoformat(ts)
                last_ts = _alert_last_ts.get(location)
                cooldown_ok = (
                    last_ts is None or
                    (now_dt - last_ts).total_seconds() >= ALERT_COOLDOWN_SECONDS
                )

                if consec >= ALERT_MIN_CONSECUTIVE and cooldown_ok:
                    top_fault, top_prob = None, None
                    if rf_class:
                        top_fault, top_prob = max(rf_class.items(), key=lambda x: x[1])

                    severity = "CRITICAL" if (top_prob or 0) > ALERT_CRITICAL_RF_GATE else "WARNING"
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
                    _alert_last_ts[location]   = now_dt
                    _alert_consec[location]    = 0
                    _alert_top_fault[location] = None

                detected_fault = None
                if if_label == -1 and rf_class:
                    _top_fault, _top_prob = max(rf_class.items(), key=lambda x: x[1])
                    if _top_prob >= 0.60:
                        detected_fault = _top_fault

                    if _top_prob >= LATCH_THRESHOLD:
                        prev_latch_fault = _latch_streak.get(location)
                        if prev_latch_fault == _top_fault:
                            _latch_consec[location] = _latch_consec.get(location, 0) + 1
                        else:
                            _latch_consec[location] = 1
                            _latch_streak[location] = _top_fault
                    else:
                        _latch_consec[location] = 0
                        _latch_streak.pop(location, None)

                    if _latch_consec.get(location, 0) >= LATCH_MIN_CONSECUTIVE:
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
                else:
                    _latch_consec.pop(location, None)
                    _latch_streak.pop(location, None)

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

            await _broadcast({
                "type": "state",
                "locations": location_states,
                "timestamp": ts,
            })

            # Data retention: prune oldest rows if over limit
            max_rows = settings_manager.get("max_stored_rows", 50000)
            if max_rows and db.get_row_count() > max_rows:
                db.prune_old_rows(int(max_rows))

        except Exception as e:
            print(f"[loop] Error: {e}")

        await asyncio.sleep(settings_manager.get("tick_interval_seconds", constants.DATA_GENERATION_INTERVAL))


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings_manager.apply_to_trend_detector(trend_detector)
    settings_manager.apply_to_dqn(dqn)
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# REST API routes
# ---------------------------------------------------------------------------

@app.get("/api/config")
def get_config():
    return {
        "locations": constants.LOCATIONS,
        "location_positions": constants.LOCATION_POSITIONS,
        "subsystem_parameters": constants.SUBSYSTEM_PARAMETERS,
        "parameter_nominal_ranges": constants.PARAMETER_NOMINAL_RANGES,
        "parameter_units": constants.PARAMETER_UNITS,
        "faults": list(constants.FAULT_IMPACT_SEVERITY.keys()),
        "fault_impacts": {
            fault: list(data["impacts"].keys())
            for fault, data in constants.FAULT_IMPACT_SEVERITY.items()
        },
        "actions": constants.ACTIONS_TO_TAKE,
        "fault_precursor_hours": constants.FAULT_PRECURSOR_HOURS,
        "ml_enabled":   ml.enabled,
        "lstm_enabled": lstm.enabled,
        "dqn_enabled":  dqn.enabled,
    }


@app.get("/api/locations")
def get_locations():
    return db.get_all_location_states()


@app.get("/api/location/{location_name}/latest")
def get_latest(location_name: str):
    if location_name not in constants.LOCATIONS:
        raise HTTPException(404, f"Location {location_name!r} not found")
    return db.get_latest_reading(location_name)


@app.get("/api/location/{location_name}/history")
def get_history(location_name: str, parameter: str, n: int = 50):
    if location_name not in constants.LOCATIONS:
        raise HTTPException(404, f"Location {location_name!r} not found")
    n = min(n, 1000)
    return db.get_history(location_name, parameter, n)


@app.get("/api/location/{location_name}/readings")
def get_readings(location_name: str, n: int = 20):
    if location_name not in constants.LOCATIONS:
        raise HTTPException(404, f"Location {location_name!r} not found")
    n = min(n, 500)
    return db.get_recent_readings(location_name, n)


@app.get("/api/subsystems")
def get_subsystems():
    results = {}
    for subsystem, params in constants.SUBSYSTEM_PARAMETERS.items():
        results[subsystem] = {}
        for param in params:
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
def inject_fault(req: FaultRequest, request: Request):
    _require_auth(request)
    if req.location not in constants.LOCATIONS:
        raise HTTPException(404, f"Location {req.location!r} not found")
    if req.fault not in constants.FAULT_IMPACT_SEVERITY:
        raise HTTPException(400, f"Unknown fault {req.fault!r}")
    db.insert_fault(req.fault, req.location)
    lstm.clear_buffer(req.location)
    return {"status": "ok", "location": req.location, "fault": req.fault}


@app.delete("/api/faults")
def clear_all_faults(request: Request):
    _require_auth(request)
    db.clear_faults()
    generator.reset_drift()
    lstm.clear_buffer()
    _alert_consec.clear()
    _alert_last_ts.clear()
    _alert_top_fault.clear()
    _latched_fault.clear()
    _latch_alerted.clear()
    _latch_consec.clear()
    _latch_streak.clear()
    return {"status": "ok"}


@app.delete("/api/faults/{location_name}")
def clear_location_fault(location_name: str, request: Request):
    _require_auth(request)
    if location_name not in constants.LOCATIONS:
        raise HTTPException(404, f"Location {location_name!r} not found")
    db.clear_fault_for_location(location_name)
    generator.reset_drift(location_name)
    lstm.clear_buffer(location_name)
    _alert_consec.pop(location_name, None)
    _alert_last_ts.pop(location_name, None)
    _alert_top_fault.pop(location_name, None)
    _latched_fault.pop(location_name, None)
    _latch_alerted.discard(location_name)
    _latch_consec.pop(location_name, None)
    _latch_streak.pop(location_name, None)
    return {"status": "ok", "location": location_name}


@app.delete("/api/faults/latch/{location_name}")
def resolve_latched_fault(location_name: str):
    if location_name not in constants.LOCATIONS:
        raise HTTPException(404, f"Location {location_name!r} not found")
    _latched_fault.pop(location_name, None)
    _latch_alerted.discard(location_name)
    _latch_consec.pop(location_name, None)
    _latch_streak.pop(location_name, None)
    return {"status": "ok", "location": location_name}


@app.delete("/api/data")
def clear_data(request: Request):
    _require_auth(request)
    db.clear_data()
    db.clear_alerts()
    generator.reset_drift()
    generator._mission_seconds = 0.0
    _alert_consec.clear()
    _alert_last_ts.clear()
    _alert_top_fault.clear()
    _latch_consec.clear()
    _latch_streak.clear()
    return {"status": "ok", "message": "All sensor data, alerts, and drift state cleared"}


# ---------------------------------------------------------------------------
# Prediction (LSTM)
# ---------------------------------------------------------------------------

@app.get("/api/location/{location_name}/prediction")
def get_prediction(location_name: str):
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
    elapsed_hours = generator.mission_elapsed_hours
    elapsed_weeks = elapsed_hours / 168.0

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
    if location_name not in constants.LOCATIONS:
        raise HTTPException(404, f"Location {location_name!r} not found")
    n = min(n, 500)

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
    limit = min(limit, 500)
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
# AI Chat
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    messages: list[dict]
    model: str = "mistral"


@app.get("/api/ai/status")
def ai_status():
    backend = ai_analyst.get_backend()
    return {
        "backend":          backend,
        "ollama_available": backend == "ollama",
        "groq_configured":  bool(os.getenv("GROQ_API_KEY")),
    }


@app.post("/api/ai/chat")
async def ai_chat(req: ChatRequest):
    async def generate():
        loop = asyncio.get_event_loop()
        token_q: asyncio.Queue = asyncio.Queue()

        def _run():
            try:
                backend, gen = ai_analyst.chat_stream(req.messages, req.model, db)
                loop.call_soon_threadsafe(token_q.put_nowait, {"backend": backend})
                for token in gen:
                    loop.call_soon_threadsafe(token_q.put_nowait, {"token": token})
            except Exception as exc:
                loop.call_soon_threadsafe(token_q.put_nowait, {"error": str(exc)})
            finally:
                loop.call_soon_threadsafe(token_q.put_nowait, {"done": True})

        fut = loop.run_in_executor(None, _run)
        while True:
            item = await token_q.get()
            yield f"data: {json.dumps(item)}\n\n"
            if item.get("done") or item.get("error"):
                break
        await fut

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    await websocket.accept()
    _ws_clients.add(websocket)

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
            await websocket.receive_text()
    except WebSocketDisconnect:
        _ws_clients.discard(websocket)
    except Exception:
        _ws_clients.discard(websocket)


# ---------------------------------------------------------------------------
# Auth + Settings
# ---------------------------------------------------------------------------

import secrets as _secrets
from jose import jwt as _jwt, JWTError

_JWT_SECRET   = settings_manager.get_jwt_secret()
_JWT_ALG      = "HS256"
_JWT_TTL_MIN  = 30
_REVOKED_JTIS: dict[str, float] = {}  # jti -> expiry unix timestamp

# Brute-force protection: track failed login timestamps per IP
_login_attempts: dict[str, list] = defaultdict(list)
_LOGIN_MAX_ATTEMPTS   = 5
_LOGIN_WINDOW_SECONDS = 300


def _make_token() -> str:
    jti = _secrets.token_hex(16)
    payload = {
        "sub": "admin",
        "jti": jti,
        "exp": datetime.utcnow() + timedelta(minutes=_JWT_TTL_MIN),
    }
    return _jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALG)


def _cleanup_revoked():
    now = _time.time()
    expired = [jti for jti, exp in _REVOKED_JTIS.items() if exp < now]
    for jti in expired:
        del _REVOKED_JTIS[jti]


def _verify_token(token: str) -> bool:
    try:
        payload = _jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALG])
        jti = payload.get("jti")
        if jti and jti in _REVOKED_JTIS:
            return False
        return True
    except JWTError:
        return False


def _require_auth(request: Request):
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or not _verify_token(auth[7:]):
        raise HTTPException(401, "Unauthorized")


def _check_rate_limit(ip: str) -> bool:
    now = _time.time()
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < _LOGIN_WINDOW_SECONDS]
    return len(_login_attempts[ip]) < _LOGIN_MAX_ATTEMPTS


class LoginRequest(BaseModel):
    password: str

class PasswordChangeRequest(BaseModel):
    current: str
    new_password: str

class SettingsUpdateRequest(BaseModel):
    updates: dict

class ExportedClearRequest(BaseModel):
    max_id: int


@app.post("/api/auth/login")
def auth_login(req: LoginRequest, request: Request):
    ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(ip):
        raise HTTPException(429, "Too many failed login attempts — wait 5 minutes")
    if not settings_manager.verify_password(req.password):
        _login_attempts[ip].append(_time.time())
        raise HTTPException(401, "Invalid password")
    _login_attempts.pop(ip, None)
    return {"token": _make_token(), "ttl_minutes": _JWT_TTL_MIN}


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            payload = _jwt.decode(auth[7:], _JWT_SECRET, algorithms=[_JWT_ALG])
            jti = payload["jti"]
            exp_ts = float(payload["exp"])
            _REVOKED_JTIS[jti] = exp_ts
            _cleanup_revoked()
        except JWTError:
            pass
    return {"status": "ok"}


@app.get("/api/settings")
def get_settings(request: Request):
    _require_auth(request)
    s = dict(settings_manager._settings)
    s.pop("password_hash", None)
    s.pop("jwt_secret", None)
    s["groq_key_set"] = bool(s.pop("groq_api_key_enc", None))
    return s


@app.patch("/api/settings/alerts")
def save_alert_settings(req: SettingsUpdateRequest, request: Request):
    _require_auth(request)
    allowed = {"alert_min_consecutive", "alert_cooldown_seconds",
               "alert_critical_rf_gate", "latch_threshold",
               "latch_min_consecutive", "dqn_rf_bypass_threshold"}
    filtered = {k: v for k, v in req.updates.items() if k in allowed}
    settings_manager.set_and_save(filtered)
    import sys
    settings_manager.apply_to_main(sys.modules[__name__])
    settings_manager.apply_to_dqn(dqn)
    return {"status": "ok"}


@app.patch("/api/settings/trends")
def save_trend_settings(req: SettingsUpdateRequest, request: Request):
    _require_auth(request)
    allowed = {"mk_p_threshold", "mk_tau_advisory", "mk_tau_warning",
               "slope_magnitude_gate", "cusum_threshold", "cusum_baseline_pct",
               "zscore_threshold", "zscore_single_threshold", "zscore_window"}
    filtered = {k: v for k, v in req.updates.items() if k in allowed}
    settings_manager.set_and_save(filtered)
    settings_manager.apply_to_trend_detector(trend_detector)
    return {"status": "ok"}


@app.patch("/api/settings/generation")
def save_generation_settings(req: SettingsUpdateRequest, request: Request):
    _require_auth(request)
    allowed = {"tick_interval_seconds", "noise_scale",
               "crew_event_frequency", "fault_injection_enabled"}
    filtered = {k: v for k, v in req.updates.items() if k in allowed}
    settings_manager.set_and_save(filtered)
    return {"status": "ok"}


@app.patch("/api/settings/display")
def save_display_settings(req: SettingsUpdateRequest, request: Request):
    _require_auth(request)
    allowed = {"mission_start_iso", "dashboard_refresh_ms",
               "chat_max_stored", "trends_default_n", "detail_default_n",
               "max_stored_rows"}
    filtered = {k: v for k, v in req.updates.items() if k in allowed}
    settings_manager.set_and_save(filtered)
    return {"status": "ok"}


@app.patch("/api/settings/integrations/groq")
def save_groq_key(req: SettingsUpdateRequest, request: Request):
    _require_auth(request)
    key = req.updates.get("groq_api_key", "").strip()
    settings_manager.set_groq_key(key if key else None)
    if key:
        os.environ["GROQ_API_KEY"] = key
    elif "GROQ_API_KEY" in os.environ:
        del os.environ["GROQ_API_KEY"]
    return {"status": "ok"}


@app.post("/api/settings/integrations/groq/test")
def test_groq_key(request: Request):
    _require_auth(request)
    key = settings_manager.get_groq_key()
    if not key:
        return {"ok": False, "error": "No key stored"}
    try:
        from groq import Groq
        client = Groq(api_key=key)
        client.models.list()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/settings/security/change-password")
def change_password(req: PasswordChangeRequest, request: Request):
    _require_auth(request)
    if not settings_manager.change_password(req.current, req.new_password):
        raise HTTPException(400, "Current password incorrect")
    return {"status": "ok"}


@app.post("/api/settings/security/revoke-all")
def revoke_all_sessions(request: Request):
    _require_auth(request)
    global _JWT_SECRET
    _JWT_SECRET = _secrets.token_hex(32)
    settings_manager.set_and_save({"jwt_secret": _JWT_SECRET})
    _REVOKED_JTIS.clear()
    return {"status": "ok"}


@app.get("/api/settings/ml/status")
def ml_model_status(request: Request):
    _require_auth(request)
    import sklearn
    return {
        "sklearn_version": sklearn.__version__,
        "ml_enabled":   ml.enabled,
        "lstm_enabled": lstm.enabled,
        "dqn_enabled":  dqn.enabled,
    }


@app.delete("/api/settings/data/sensor")
def clear_sensor_data_settings(request: Request):
    _require_auth(request)
    db.clear_data()
    lstm.clear_buffer()
    return {"status": "ok"}


@app.delete("/api/settings/data/alerts")
def clear_alert_data(request: Request):
    _require_auth(request)
    db.clear_alerts()
    return {"status": "ok"}


@app.delete("/api/settings/data/faults")
def clear_fault_data_settings(request: Request):
    _require_auth(request)
    global _latched_fault, _latch_alerted, _latch_consec, _latch_streak
    global _alert_consec, _alert_last_ts, _alert_top_fault
    _latched_fault  = {}
    _latch_alerted  = set()
    _latch_consec   = {}
    _latch_streak   = {}
    _alert_consec   = {}
    _alert_last_ts  = {}
    _alert_top_fault = {}
    return {"status": "ok"}


@app.delete("/api/settings/data/lstm")
def clear_lstm_buffers(request: Request):
    _require_auth(request)
    lstm.clear_buffer()
    return {"status": "ok"}


@app.get("/api/settings/data/export/csv")
def export_sensor_csv(request: Request):
    _require_auth(request)
    watermark = db.get_export_max_id() or 0
    row_count = db.get_row_count_up_to(watermark) if watermark else 0

    def _generate():
        yield "id,timestamp,location,parameter,value,is_anomalous,rf_prediction,rf_confidence\n"
        if watermark == 0:
            return
        import sqlite3 as _sq
        conn = _sq.connect(db.db_path, check_same_thread=False)
        conn.row_factory = _sq.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            cursor = conn.execute(
                """SELECT gd.id, gd.timestamp, l.location_name, gd.data,
                          al.isolation_forest_label,
                          al.random_forest_classification
                   FROM generated_data gd
                   JOIN locations l ON l.id = gd.location_id
                   LEFT JOIN anomaly_labels al ON al.data_row_id = gd.id
                   WHERE gd.id <= ?
                   ORDER BY gd.id""",
                (watermark,),
            )
            for row in cursor:
                data_dict = json.loads(row["data"])
                is_anom = row["isolation_forest_label"] == -1 \
                    if row["isolation_forest_label"] is not None else False
                rf_raw = json.loads(row["random_forest_classification"] or "null")
                rf_pred = rf_raw.get("prediction", "") if isinstance(rf_raw, dict) else ""
                rf_conf = rf_raw.get("confidence", "") if isinstance(rf_raw, dict) else ""
                loc = row["location_name"].replace('"', '""')
                ts  = str(row["timestamp"]).replace('"', '""')
                for param, value in data_dict.items():
                    p = param.replace('"', '""')
                    yield f'{row["id"]},"{ts}","{loc}","{p}",{value},{is_anom},"{rf_pred}",{rf_conf}\n'
        finally:
            conn.close()

    fname = f"aura_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    headers = {
        "Content-Disposition": f'attachment; filename="{fname}"',
        "X-Export-Max-Id":    str(watermark),
        "X-Export-Row-Count": str(row_count),
        "Access-Control-Expose-Headers": "X-Export-Max-Id, X-Export-Row-Count",
    }
    return StreamingResponse(_generate(), media_type="text/csv", headers=headers)


@app.delete("/api/settings/data/exported")
def clear_exported_data(req: ExportedClearRequest, request: Request):
    _require_auth(request)
    deleted = db.clear_exported_data(req.max_id)
    if db.get_export_max_id() is None:
        lstm.clear_buffer()
    return {"deleted_rows": deleted}


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
