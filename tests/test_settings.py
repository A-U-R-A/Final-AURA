"""
Tests for app/settings_manager.py — persistence, encryption, JWT secret,
password hashing, apply_ helpers, and path configuration.
"""

import json
import pytest
from pathlib import Path


@pytest.fixture
def fresh_manager(tmp_path):
    """Isolated settings manager instance backed by tmp files."""
    import importlib
    import app.settings_manager as sm

    orig_settings_path  = sm._SETTINGS_PATH
    orig_fernet_path    = sm._FERNET_KEY_PATH
    orig_settings       = dict(sm._settings)
    orig_fernet         = sm._fernet

    sm._SETTINGS_PATH   = tmp_path / "settings.json"
    sm._FERNET_KEY_PATH = tmp_path / ".fernet.key"
    sm._fernet          = sm._get_fernet()
    sm._settings        = dict(sm.DEFAULT_SETTINGS)
    sm.init_password("testpass")

    yield sm

    sm._SETTINGS_PATH   = orig_settings_path
    sm._FERNET_KEY_PATH = orig_fernet_path
    sm._settings        = orig_settings
    sm._fernet          = orig_fernet


# ── PATHS ─────────────────────────────────────────────────────────────────────

class TestPaths:
    def test_settings_path_in_data_dir(self):
        import app.settings_manager as sm
        assert sm._SETTINGS_PATH.parts[-2] == "data"

    def test_fernet_key_path_in_data_dir(self):
        import app.settings_manager as sm
        assert sm._FERNET_KEY_PATH.parts[-2] == "data"

    def test_settings_path_is_json(self):
        import app.settings_manager as sm
        assert sm._SETTINGS_PATH.suffix == ".json"


# ── DEFAULT SETTINGS ──────────────────────────────────────────────────────────

class TestDefaults:
    def test_default_settings_not_empty(self):
        from app.settings_manager import DEFAULT_SETTINGS
        assert len(DEFAULT_SETTINGS) > 0

    def test_required_keys_present(self):
        from app.settings_manager import DEFAULT_SETTINGS
        required = {
            "alert_min_consecutive", "alert_cooldown_seconds",
            "alert_critical_rf_gate", "latch_threshold",
            "latch_min_consecutive", "tick_interval_seconds",
            "max_stored_rows", "jwt_secret",
            "mk_p_threshold", "cusum_threshold",
            "dashboard_refresh_ms",
        }
        for key in required:
            assert key in DEFAULT_SETTINGS, f"Missing default: {key!r}"

    def test_max_stored_rows_default_reasonable(self):
        from app.settings_manager import DEFAULT_SETTINGS
        assert DEFAULT_SETTINGS["max_stored_rows"] >= 1000

    def test_tick_interval_positive(self):
        from app.settings_manager import DEFAULT_SETTINGS
        assert DEFAULT_SETTINGS["tick_interval_seconds"] > 0

    def test_alert_min_consecutive_positive(self):
        from app.settings_manager import DEFAULT_SETTINGS
        assert DEFAULT_SETTINGS["alert_min_consecutive"] > 0


# ── LOAD / SAVE / GET / SET ───────────────────────────────────────────────────

class TestPersistence:
    def test_load_returns_dict(self, fresh_manager):
        result = fresh_manager.load()
        assert isinstance(result, dict)

    def test_get_existing_key(self, fresh_manager):
        val = fresh_manager.get("tick_interval_seconds")
        assert val is not None

    def test_get_missing_key_returns_default(self, fresh_manager):
        assert fresh_manager.get("__nonexistent__", 42) == 42

    def test_set_and_save_persists(self, fresh_manager, tmp_path):
        fresh_manager._SETTINGS_PATH = tmp_path / "settings.json"
        fresh_manager.set_and_save({"tick_interval_seconds": 999})
        fresh_manager.load()
        assert fresh_manager.get("tick_interval_seconds") == 999

    def test_saved_file_is_valid_json(self, fresh_manager, tmp_path):
        path = tmp_path / "settings_check.json"
        fresh_manager._SETTINGS_PATH = path
        fresh_manager.save()
        data = json.loads(path.read_text())
        assert isinstance(data, dict)

    def test_load_merges_missing_defaults(self, fresh_manager, tmp_path):
        path = tmp_path / "partial.json"
        path.write_text(json.dumps({"tick_interval_seconds": 5}))
        fresh_manager._SETTINGS_PATH = path
        settings = fresh_manager.load()
        # Should have both persisted value and defaults for missing keys
        assert settings["tick_interval_seconds"] == 5
        assert "alert_min_consecutive" in settings


# ── PASSWORD ──────────────────────────────────────────────────────────────────

class TestPassword:
    def test_verify_correct_password(self, fresh_manager):
        assert fresh_manager.verify_password("testpass") is True

    def test_verify_wrong_password(self, fresh_manager):
        assert fresh_manager.verify_password("wrongpass") is False

    def test_verify_empty_password(self, fresh_manager):
        assert fresh_manager.verify_password("") is False

    def test_change_password_success(self, fresh_manager):
        result = fresh_manager.change_password("testpass", "newpass")
        assert result is True
        assert fresh_manager.verify_password("newpass") is True

    def test_change_password_wrong_current(self, fresh_manager):
        result = fresh_manager.change_password("wrongcurrent", "newpass")
        assert result is False

    def test_change_password_old_fails_after(self, fresh_manager):
        fresh_manager.change_password("testpass", "newpass2")
        assert fresh_manager.verify_password("testpass") is False

    def test_password_hash_not_stored_in_plaintext(self, fresh_manager, tmp_path):
        path = tmp_path / "pw_test.json"
        fresh_manager._SETTINGS_PATH = path
        fresh_manager.save()
        raw = path.read_text()
        assert "testpass" not in raw

    def test_init_password_creates_hash(self, fresh_manager):
        assert fresh_manager._settings.get("password_hash") is not None


# ── ENCRYPTION ────────────────────────────────────────────────────────────────

class TestEncryption:
    def test_encrypt_decrypt_roundtrip(self, fresh_manager):
        plain = "my-secret-api-key-xyz"
        enc = fresh_manager.encrypt_value(plain)
        assert enc != plain
        assert fresh_manager.decrypt_value(enc) == plain

    def test_encrypt_different_each_call(self, fresh_manager):
        enc1 = fresh_manager.encrypt_value("value")
        enc2 = fresh_manager.encrypt_value("value")
        # Fernet uses random IV so ciphertexts differ
        assert enc1 != enc2

    def test_fernet_key_created_if_missing(self, fresh_manager, tmp_path):
        new_key_path = tmp_path / "new.fernet.key"
        fresh_manager._FERNET_KEY_PATH = new_key_path
        fresh_manager._fernet = fresh_manager._get_fernet()
        assert new_key_path.exists()

    def test_fernet_key_file_permissions(self, fresh_manager, tmp_path):
        key_path = tmp_path / "perm_test.key"
        fresh_manager._FERNET_KEY_PATH = key_path
        fresh_manager._get_fernet()
        import stat
        mode = oct(stat.S_IMODE(key_path.stat().st_mode))
        assert mode == oct(0o600), f"Key file permissions {mode} not 600"


# ── JWT SECRET ────────────────────────────────────────────────────────────────

class TestJwtSecret:
    def test_get_jwt_secret_returns_string(self, fresh_manager):
        secret = fresh_manager.get_jwt_secret()
        assert isinstance(secret, str)
        assert len(secret) >= 32

    def test_get_jwt_secret_stable(self, fresh_manager):
        s1 = fresh_manager.get_jwt_secret()
        s2 = fresh_manager.get_jwt_secret()
        assert s1 == s2

    def test_get_jwt_secret_creates_and_persists(self, fresh_manager, tmp_path):
        fresh_manager._settings["jwt_secret"] = None
        path = tmp_path / "jwt_test.json"
        fresh_manager._SETTINGS_PATH = path
        secret = fresh_manager.get_jwt_secret()
        assert secret is not None
        fresh_manager.load()
        assert fresh_manager._settings.get("jwt_secret") == secret

    def test_groq_key_roundtrip(self, fresh_manager):
        fresh_manager.set_groq_key("test-groq-key-abc123")
        retrieved = fresh_manager.get_groq_key()
        assert retrieved == "test-groq-key-abc123"

    def test_groq_key_none_clears(self, fresh_manager):
        fresh_manager.set_groq_key("some-key")
        fresh_manager.set_groq_key(None)
        assert fresh_manager.get_groq_key() is None


# ── APPLY HELPERS ─────────────────────────────────────────────────────────────

class TestApplyHelpers:
    def test_apply_to_main(self, fresh_manager):
        import types
        mock_main = types.SimpleNamespace(
            ALERT_MIN_CONSECUTIVE=0,
            ALERT_COOLDOWN_SECONDS=0,
            ALERT_CRITICAL_RF_GATE=0.0,
            LATCH_THRESHOLD=0.0,
            LATCH_MIN_CONSECUTIVE=0,
        )
        fresh_manager._settings["alert_min_consecutive"] = 15
        fresh_manager.apply_to_main(mock_main)
        assert mock_main.ALERT_MIN_CONSECUTIVE == 15

    def test_apply_to_trend_detector(self, fresh_manager):
        import types
        mock_td = types.SimpleNamespace(
            MK_P_THRESHOLD=0.0,
            MK_TAU_ADVISORY=0.0,
            MK_TAU_WARNING=0.0,
            SLOPE_MAGNITUDE_GATE=0.0,
            CUSUM_THRESHOLD=0.0,
            CUSUM_BASELINE_PCT=0.0,
            ZSCORE_THRESHOLD=0.0,
            ZSCORE_SINGLE_THRESHOLD=0.0,
            ZSCORE_WINDOW=0,
        )
        fresh_manager._settings["mk_p_threshold"] = 0.05
        fresh_manager.apply_to_trend_detector(mock_td)
        assert mock_td.MK_P_THRESHOLD == 0.05

    def test_apply_to_dqn(self, fresh_manager):
        import types
        mock_dqn = types.SimpleNamespace(RF_BYPASS_THRESHOLD=0.0)
        fresh_manager._settings["dqn_rf_bypass_threshold"] = 0.88
        fresh_manager.apply_to_dqn(mock_dqn)
        assert mock_dqn.RF_BYPASS_THRESHOLD == 0.88
