"""
Settings manager for AURA.

Owns:
  - settings.json  (persisted config: thresholds, hashed password, encrypted API keys)
  - .fernet.key    (machine-local Fernet key — never stored in settings.json)
  - Runtime override layer: call apply_to_main() / apply_to_trend_detector() after
    any save so callers see updated values without a server restart.
"""

import json
import os
import secrets
import bcrypt
from pathlib import Path
from cryptography.fernet import Fernet

_SETTINGS_PATH  = Path(__file__).parent / "settings.json"
_FERNET_KEY_PATH = Path(__file__).parent / ".fernet.key"

# ── Fernet key (machine-local, never committed) ───────────────────────────────

def _get_fernet() -> Fernet:
    if _FERNET_KEY_PATH.exists():
        key = _FERNET_KEY_PATH.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        _FERNET_KEY_PATH.write_bytes(key)
        _FERNET_KEY_PATH.chmod(0o600)
    return Fernet(key)

_fernet = _get_fernet()


def encrypt_value(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_value(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()


# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULT_SETTINGS: dict = {
    # Auth — password hash is written on first run via init_password()
    "password_hash": None,

    # Alert thresholds (mirrors main.py module-level vars)
    "alert_min_consecutive":  10,
    "alert_cooldown_seconds": 600,
    "alert_critical_rf_gate": 0.85,
    "latch_threshold":        0.95,
    "latch_min_consecutive":  3,
    "dqn_rf_bypass_threshold": 0.92,

    # Data generation
    "tick_interval_seconds": 1,
    "noise_scale":           1.0,
    "crew_event_frequency":  "medium",
    "fault_injection_enabled": True,

    # ML
    "if_contamination": 0.1,
    "lstm_seq_len_override": None,

    # Trend detection (mirrors trend_detector.py constants)
    "mk_p_threshold":        0.01,
    "mk_tau_advisory":       0.35,
    "mk_tau_warning":        0.65,
    "slope_magnitude_gate":  0.05,
    "cusum_threshold":       7.0,
    "cusum_baseline_pct":    0.20,
    "zscore_threshold":      3.5,
    "zscore_single_threshold": 4.5,
    "zscore_window":         30,

    # Integrations — values are Fernet-encrypted when non-null
    "groq_api_key_enc": None,

    # Display
    "mission_start_iso":      None,
    "dashboard_refresh_ms":   30000,
    "chat_max_stored":        40,
    "trends_default_n":       100,
    "detail_default_n":       100,
}

# ── Load / save ───────────────────────────────────────────────────────────────

_settings: dict = {}


def load() -> dict:
    global _settings
    if _SETTINGS_PATH.exists():
        try:
            stored = json.loads(_SETTINGS_PATH.read_text())
        except Exception:
            stored = {}
    else:
        stored = {}
    # Merge: stored values win over defaults (forward-compat with new defaults)
    _settings = {**DEFAULT_SETTINGS, **stored}
    return _settings


def save() -> None:
    _SETTINGS_PATH.write_text(json.dumps(_settings, indent=2))


def get(key: str, default=None):
    return _settings.get(key, default)


def set_and_save(updates: dict) -> None:
    _settings.update(updates)
    save()


# ── Password helpers ──────────────────────────────────────────────────────────

def init_password(plaintext: str) -> None:
    """Hash and persist a new admin password (call once on first run)."""
    hashed = bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt()).decode()
    set_and_save({"password_hash": hashed})


def verify_password(plaintext: str) -> bool:
    stored = _settings.get("password_hash")
    if not stored:
        return False
    return bcrypt.checkpw(plaintext.encode(), stored.encode())


def change_password(current: str, new: str) -> bool:
    if not verify_password(current):
        return False
    hashed = bcrypt.hashpw(new.encode(), bcrypt.gensalt()).decode()
    set_and_save({"password_hash": hashed})
    return True


# ── API key helpers ───────────────────────────────────────────────────────────

def set_groq_key(plaintext: str) -> None:
    enc = encrypt_value(plaintext) if plaintext else None
    set_and_save({"groq_api_key_enc": enc})


def get_groq_key() -> str | None:
    enc = _settings.get("groq_api_key_enc")
    if not enc:
        return None
    try:
        return decrypt_value(enc)
    except Exception:
        return None


# ── Hot-reload helpers ────────────────────────────────────────────────────────
# Called after settings are saved to push new values into live module vars
# without a server restart.

def apply_to_main(main_module) -> None:
    """Update alert/latch threshold vars in the running main module."""
    main_module.ALERT_MIN_CONSECUTIVE   = int(_settings["alert_min_consecutive"])
    main_module.ALERT_COOLDOWN_SECONDS  = int(_settings["alert_cooldown_seconds"])
    main_module.ALERT_CRITICAL_RF_GATE  = float(_settings["alert_critical_rf_gate"])
    main_module.LATCH_THRESHOLD         = float(_settings["latch_threshold"])
    main_module.LATCH_MIN_CONSECUTIVE   = int(_settings["latch_min_consecutive"])


def apply_to_dqn(dqn_instance) -> None:
    dqn_instance.RF_BYPASS_THRESHOLD = float(_settings["dqn_rf_bypass_threshold"])


def apply_to_trend_detector(td_module) -> None:
    td_module.MK_P_THRESHOLD          = float(_settings["mk_p_threshold"])
    td_module.MK_TAU_ADVISORY         = float(_settings["mk_tau_advisory"])
    td_module.MK_TAU_WARNING          = float(_settings["mk_tau_warning"])
    td_module.SLOPE_MAGNITUDE_GATE    = float(_settings["slope_magnitude_gate"])
    td_module.CUSUM_THRESHOLD         = float(_settings["cusum_threshold"])
    td_module.CUSUM_BASELINE_PCT      = float(_settings["cusum_baseline_pct"])
    td_module.ZSCORE_THRESHOLD        = float(_settings["zscore_threshold"])
    td_module.ZSCORE_SINGLE_THRESHOLD = float(_settings["zscore_single_threshold"])
    td_module.ZSCORE_WINDOW           = int(_settings["zscore_window"])


# ── Bootstrap ─────────────────────────────────────────────────────────────────

load()

# If no password is set yet, create a default (user must change it)
if not _settings.get("password_hash"):
    init_password("admin")
    print("[Settings] Default admin password set to 'admin' — change it in Settings > Security")

# Push GROQ key to environment so ai_analyst.py picks it up
_groq = get_groq_key()
if _groq:
    os.environ["GROQ_API_KEY"] = _groq
