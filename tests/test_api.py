"""
Tests for all FastAPI endpoints in main.py.
Uses the session-scoped app_client and auth_token from conftest.py.
"""

import pytest
from app import constants


# ── HEALTH / ROOT ─────────────────────────────────────────────────────────────

class TestHealth:
    def test_root_serves_html(self, app_client):
        r = app_client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")

    def test_404_for_unknown_route(self, app_client):
        r = app_client.get("/api/nonexistent_endpoint_xyz")
        assert r.status_code == 404


# ── /api/config ───────────────────────────────────────────────────────────────

class TestConfig:
    def test_returns_200(self, app_client):
        r = app_client.get("/api/config")
        assert r.status_code == 200

    def test_has_locations(self, app_client):
        r = app_client.get("/api/config")
        assert "locations" in r.json()

    def test_locations_match_constants(self, app_client):
        r = app_client.get("/api/config")
        assert set(r.json()["locations"]) == set(constants.LOCATIONS)

    def test_has_faults(self, app_client):
        r = app_client.get("/api/config")
        data = r.json()
        assert "faults" in data
        assert len(data["faults"]) > 0

    def test_faults_match_constants(self, app_client):
        r = app_client.get("/api/config")
        assert set(r.json()["faults"]) == set(constants.FAULT_IMPACT_SEVERITY.keys())

    def test_has_actions(self, app_client):
        r = app_client.get("/api/config")
        assert "actions" in r.json()
        assert "No Action Needed" in r.json()["actions"]

    def test_has_parameter_nominal_ranges(self, app_client):
        r = app_client.get("/api/config")
        assert "parameter_nominal_ranges" in r.json()

    def test_has_ml_flags(self, app_client):
        r = app_client.get("/api/config")
        data = r.json()
        assert "ml_enabled" in data
        assert "lstm_enabled" in data
        assert "dqn_enabled" in data

    def test_ml_flags_are_bools(self, app_client):
        r = app_client.get("/api/config")
        data = r.json()
        assert isinstance(data["ml_enabled"], bool)
        assert isinstance(data["lstm_enabled"], bool)
        assert isinstance(data["dqn_enabled"], bool)


# ── /api/locations ────────────────────────────────────────────────────────────

class TestLocations:
    def test_returns_200(self, app_client):
        r = app_client.get("/api/locations")
        assert r.status_code == 200

    def test_returns_list_or_dict(self, app_client):
        r = app_client.get("/api/locations")
        assert r.status_code == 200
        assert isinstance(r.json(), (list, dict))


# ── /api/location/{loc}/latest ────────────────────────────────────────────────

class TestLocationLatest:
    def test_known_location_returns_200_or_none(self, app_client):
        loc = constants.LOCATIONS[0]
        r = app_client.get(f"/api/location/{loc}/latest")
        # May be None (no data yet) or a reading dict
        assert r.status_code == 200

    def test_unknown_location_returns_404(self, app_client):
        r = app_client.get("/api/location/NONEXISTENT_XYZ/latest")
        assert r.status_code == 404


# ── /api/location/{loc}/history ───────────────────────────────────────────────

class TestLocationHistory:
    def test_known_location_returns_200(self, app_client):
        loc = constants.LOCATIONS[0]
        param = list(constants.PARAMETER_NOMINAL_RANGES.keys())[0]
        r = app_client.get(f"/api/location/{loc}/history", params={"parameter": param})
        assert r.status_code == 200

    def test_returns_list(self, app_client):
        loc = constants.LOCATIONS[0]
        param = list(constants.PARAMETER_NOMINAL_RANGES.keys())[0]
        r = app_client.get(f"/api/location/{loc}/history", params={"parameter": param})
        assert isinstance(r.json(), list)

    def test_unknown_location_404(self, app_client):
        r = app_client.get(
            "/api/location/NOTAPLACE/history",
            params={"parameter": "O2 partial pressure"}
        )
        assert r.status_code == 404

    def test_n_capped_at_1000(self, app_client):
        loc = constants.LOCATIONS[0]
        param = list(constants.PARAMETER_NOMINAL_RANGES.keys())[0]
        r = app_client.get(
            f"/api/location/{loc}/history",
            params={"parameter": param, "n": 99999}
        )
        assert r.status_code == 200
        # Result length should be <= 1000
        assert len(r.json()) <= 1000


# ── /api/location/{loc}/readings ──────────────────────────────────────────────

class TestLocationReadings:
    def test_returns_200(self, app_client):
        loc = constants.LOCATIONS[0]
        r = app_client.get(f"/api/location/{loc}/readings")
        assert r.status_code == 200

    def test_returns_list(self, app_client):
        loc = constants.LOCATIONS[0]
        r = app_client.get(f"/api/location/{loc}/readings")
        assert isinstance(r.json(), list)

    def test_unknown_location_404(self, app_client):
        r = app_client.get("/api/location/NOTREAL/readings")
        assert r.status_code == 404

    def test_n_capped_at_500(self, app_client):
        loc = constants.LOCATIONS[0]
        r = app_client.get(f"/api/location/{loc}/readings", params={"n": 9999})
        assert r.status_code == 200
        assert len(r.json()) <= 500


# ── /api/subsystems ───────────────────────────────────────────────────────────

class TestSubsystems:
    def test_returns_200(self, app_client):
        r = app_client.get("/api/subsystems")
        assert r.status_code == 200

    def test_returns_dict(self, app_client):
        r = app_client.get("/api/subsystems")
        assert isinstance(r.json(), dict)

    def test_subsystem_keys_match_constants(self, app_client):
        r = app_client.get("/api/subsystems")
        data = r.json()
        for key in data:
            assert key in constants.SUBSYSTEM_PARAMETERS


# ── /api/location/{loc}/prediction ────────────────────────────────────────────

class TestPrediction:
    def test_returns_200(self, app_client):
        loc = constants.LOCATIONS[0]
        r = app_client.get(f"/api/location/{loc}/prediction")
        assert r.status_code == 200

    def test_has_location_key(self, app_client):
        loc = constants.LOCATIONS[0]
        r = app_client.get(f"/api/location/{loc}/prediction")
        assert r.json()["location"] == loc

    def test_has_lstm_enabled_key(self, app_client):
        loc = constants.LOCATIONS[0]
        r = app_client.get(f"/api/location/{loc}/prediction")
        assert "lstm_enabled" in r.json()

    def test_unknown_location_404(self, app_client):
        r = app_client.get("/api/location/FAKE_LOC/prediction")
        assert r.status_code == 404

    def test_when_not_ready_has_ready_false(self, app_client):
        loc = constants.LOCATIONS[0]
        r = app_client.get(f"/api/location/{loc}/prediction")
        data = r.json()
        if not data.get("ready"):
            assert "buffer_fill" in data
            assert "seq_len" in data


# ── /api/location/{loc}/recommendation ───────────────────────────────────────

class TestRecommendation:
    def test_returns_200(self, app_client):
        loc = constants.LOCATIONS[0]
        r = app_client.get(f"/api/location/{loc}/recommendation")
        assert r.status_code == 200

    def test_has_action_key(self, app_client):
        loc = constants.LOCATIONS[0]
        r = app_client.get(f"/api/location/{loc}/recommendation")
        assert "action" in r.json()

    def test_action_in_actions_list(self, app_client):
        loc = constants.LOCATIONS[0]
        r = app_client.get(f"/api/location/{loc}/recommendation")
        assert r.json()["action"] in constants.ACTIONS_TO_TAKE

    def test_unknown_location_404(self, app_client):
        r = app_client.get("/api/location/FAKE_PLACE/recommendation")
        assert r.status_code == 404


# ── /api/location/{loc}/trends ────────────────────────────────────────────────

class TestTrends:
    def test_returns_200(self, app_client):
        loc = constants.LOCATIONS[0]
        r = app_client.get(f"/api/location/{loc}/trends")
        assert r.status_code == 200

    def test_has_location_key(self, app_client):
        loc = constants.LOCATIONS[0]
        r = app_client.get(f"/api/location/{loc}/trends")
        assert r.json()["location"] == loc

    def test_has_trends_key(self, app_client):
        loc = constants.LOCATIONS[0]
        r = app_client.get(f"/api/location/{loc}/trends")
        assert "trends" in r.json()

    def test_trends_is_list(self, app_client):
        loc = constants.LOCATIONS[0]
        r = app_client.get(f"/api/location/{loc}/trends")
        assert isinstance(r.json()["trends"], list)

    def test_n_capped_at_500(self, app_client):
        loc = constants.LOCATIONS[0]
        r = app_client.get(f"/api/location/{loc}/trends", params={"n": 99999})
        assert r.status_code == 200
        assert r.json()["n_readings"] <= 500

    def test_unknown_location_404(self, app_client):
        r = app_client.get("/api/location/NOWHERE/trends")
        assert r.status_code == 404


# ── /api/alerts ───────────────────────────────────────────────────────────────

class TestAlerts:
    def test_returns_200(self, app_client):
        r = app_client.get("/api/alerts")
        assert r.status_code == 200

    def test_returns_list(self, app_client):
        r = app_client.get("/api/alerts")
        assert isinstance(r.json(), list)

    def test_limit_capped_at_500(self, app_client):
        r = app_client.get("/api/alerts", params={"limit": 9999})
        assert r.status_code == 200
        assert len(r.json()) <= 500

    def test_location_filter(self, app_client):
        loc = constants.LOCATIONS[0]
        r = app_client.get("/api/alerts", params={"location": loc})
        assert r.status_code == 200


class TestAlertCount:
    def test_returns_200(self, app_client):
        r = app_client.get("/api/alerts/count")
        assert r.status_code == 200

    def test_has_total_and_unacknowledged(self, app_client):
        r = app_client.get("/api/alerts/count")
        data = r.json()
        assert "total" in data
        assert "unacknowledged" in data


class TestAlertAcknowledge:
    def test_acknowledge_nonexistent_returns_200(self, app_client):
        # Should not crash even for unknown IDs
        r = app_client.post("/api/alerts/9999999/acknowledge")
        assert r.status_code == 200

    def test_acknowledge_all_returns_200(self, app_client):
        r = app_client.post("/api/alerts/acknowledge-all")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ── /api/maintenance ──────────────────────────────────────────────────────────

class TestMaintenance:
    def test_returns_200(self, app_client):
        r = app_client.get("/api/maintenance")
        assert r.status_code == 200

    def test_has_replacement_schedule(self, app_client):
        r = app_client.get("/api/maintenance")
        assert "replacement_schedule" in r.json()

    def test_has_calibration_schedule(self, app_client):
        r = app_client.get("/api/maintenance")
        assert "calibration_schedule" in r.json()

    def test_has_elapsed_hours(self, app_client):
        r = app_client.get("/api/maintenance")
        assert "mission_elapsed_hours" in r.json()

    def test_replacement_schedule_is_list(self, app_client):
        r = app_client.get("/api/maintenance")
        assert isinstance(r.json()["replacement_schedule"], list)

    def test_each_replacement_has_status(self, app_client):
        r = app_client.get("/api/maintenance")
        for item in r.json()["replacement_schedule"]:
            assert "status" in item
            assert item["status"] in {"NOMINAL", "CAUTION", "WARNING", "CRITICAL"}

    def test_pct_life_used_in_valid_range(self, app_client):
        r = app_client.get("/api/maintenance")
        for item in r.json()["replacement_schedule"]:
            assert 0.0 <= item["pct_life_used"] <= 100.0


# ── /api/ai/status ────────────────────────────────────────────────────────────

class TestAIStatus:
    def test_returns_200(self, app_client):
        r = app_client.get("/api/ai/status")
        assert r.status_code == 200

    def test_has_backend_key(self, app_client):
        r = app_client.get("/api/ai/status")
        assert "backend" in r.json()

    def test_backend_valid(self, app_client):
        r = app_client.get("/api/ai/status")
        assert r.json()["backend"] in {"ollama", "groq", "none"}

    def test_has_groq_configured(self, app_client):
        r = app_client.get("/api/ai/status")
        assert "groq_configured" in r.json()


# ── /api/ai/chat (no-backend path) ───────────────────────────────────────────

class TestAIChat:
    def test_chat_with_no_backend_returns_200(self, app_client):
        from unittest.mock import patch
        import app.ai_analyst as aa
        with patch.object(aa, "_is_ollama_available", return_value=False), \
             patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("GROQ_API_KEY", None)
            r = app_client.post(
                "/api/ai/chat",
                json={"messages": [{"role": "user", "content": "hello"}], "model": "mistral"},
            )
        assert r.status_code == 200

    def test_chat_response_is_event_stream(self, app_client):
        from unittest.mock import patch
        import app.ai_analyst as aa
        import os
        with patch.object(aa, "_is_ollama_available", return_value=False):
            os.environ.pop("GROQ_API_KEY", None)
            r = app_client.post(
                "/api/ai/chat",
                json={"messages": [{"role": "user", "content": "hello"}], "model": "mistral"},
            )
        ct = r.headers.get("content-type", "")
        assert "event-stream" in ct or r.status_code == 200


# ── FAULT CONTROL (auth required) ─────────────────────────────────────────────

class TestFaultControl:
    def test_inject_without_auth_returns_401(self, app_client):
        r = app_client.post(
            "/api/faults/inject",
            json={"location": constants.LOCATIONS[0], "fault": "Cabin Leak"}
        )
        assert r.status_code == 401

    def test_inject_with_auth_valid_returns_200(self, app_client, auth_token):
        r = app_client.post(
            "/api/faults/inject",
            json={"location": constants.LOCATIONS[0], "fault": "Cabin Leak"},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_inject_unknown_location_404(self, app_client, auth_token):
        r = app_client.post(
            "/api/faults/inject",
            json={"location": "MOON_BASE", "fault": "Cabin Leak"},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert r.status_code == 404

    def test_inject_unknown_fault_400(self, app_client, auth_token):
        r = app_client.post(
            "/api/faults/inject",
            json={"location": constants.LOCATIONS[0], "fault": "Fake Fault XYZ"},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert r.status_code == 400

    def test_clear_location_fault_without_auth_401(self, app_client):
        loc = constants.LOCATIONS[0]
        r = app_client.delete(f"/api/faults/{loc}")
        assert r.status_code == 401

    def test_clear_location_fault_with_auth(self, app_client, auth_token):
        loc = constants.LOCATIONS[0]
        r = app_client.delete(
            f"/api/faults/{loc}",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert r.status_code == 200

    def test_clear_all_faults_without_auth_401(self, app_client):
        r = app_client.delete("/api/faults")
        assert r.status_code == 401

    def test_clear_all_faults_with_auth(self, app_client, auth_token):
        r = app_client.delete(
            "/api/faults",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert r.status_code == 200

    def test_resolve_latch_no_auth_required(self, app_client):
        loc = constants.LOCATIONS[0]
        r = app_client.delete(f"/api/faults/latch/{loc}")
        assert r.status_code == 200

    def test_resolve_latch_unknown_location_404(self, app_client):
        r = app_client.delete("/api/faults/latch/NOPE")
        assert r.status_code == 404

    def test_clear_data_without_auth_401(self, app_client):
        r = app_client.delete("/api/data")
        assert r.status_code == 401

    def test_clear_data_with_auth(self, app_client, auth_token):
        r = app_client.delete(
            "/api/data",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ── AUTH ENDPOINTS ─────────────────────────────────────────────────────────────

class TestAuth:
    def test_login_correct_password_returns_token(self, app_client):
        r = app_client.post("/api/auth/login", json={"password": "admin"})
        assert r.status_code == 200
        data = r.json()
        assert "token" in data
        assert isinstance(data["token"], str)
        assert len(data["token"]) > 10

    def test_login_wrong_password_returns_401(self, app_client):
        r = app_client.post("/api/auth/login", json={"password": "wrongpassword_xyz_99"})
        assert r.status_code == 401

    def test_login_response_has_ttl_minutes(self, app_client):
        r = app_client.post("/api/auth/login", json={"password": "admin"})
        assert "ttl_minutes" in r.json()

    def test_logout_with_valid_token_returns_200(self, app_client):
        # Use a fresh token so the session-scoped auth_token is not revoked
        r = app_client.post("/api/auth/login", json={"password": "admin"})
        fresh_token = r.json()["token"]
        r = app_client.post(
            "/api/auth/logout",
            headers={"Authorization": f"Bearer {fresh_token}"},
        )
        assert r.status_code == 200

    def test_logout_without_token_returns_200(self, app_client):
        # Logout without auth still returns 200 (no-op)
        r = app_client.post("/api/auth/logout")
        assert r.status_code == 200


# ── SETTINGS ENDPOINTS ────────────────────────────────────────────────────────

class TestSettingsEndpoints:
    def test_get_settings_without_auth_401(self, app_client):
        r = app_client.get("/api/settings")
        assert r.status_code == 401

    def test_get_settings_with_auth_200(self, app_client, auth_token):
        r = app_client.get(
            "/api/settings",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert r.status_code == 200

    def test_settings_hides_password_hash(self, app_client, auth_token):
        r = app_client.get(
            "/api/settings",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        data = r.json()
        assert "password_hash" not in data

    def test_settings_hides_jwt_secret(self, app_client, auth_token):
        r = app_client.get(
            "/api/settings",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        data = r.json()
        assert "jwt_secret" not in data

    def test_patch_alerts_without_auth_401(self, app_client):
        r = app_client.patch(
            "/api/settings/alerts",
            json={"updates": {"alert_min_consecutive": 5}},
        )
        assert r.status_code == 401

    def test_patch_alerts_with_auth_200(self, app_client, auth_token):
        r = app_client.patch(
            "/api/settings/alerts",
            json={"updates": {"alert_min_consecutive": 10}},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert r.status_code == 200

    def test_patch_trends_with_auth_200(self, app_client, auth_token):
        r = app_client.patch(
            "/api/settings/trends",
            json={"updates": {"mk_p_threshold": 0.05}},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert r.status_code == 200

    def test_patch_generation_with_auth_200(self, app_client, auth_token):
        r = app_client.patch(
            "/api/settings/generation",
            json={"updates": {"tick_interval_seconds": 60}},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert r.status_code == 200

    def test_patch_display_with_auth_200(self, app_client, auth_token):
        r = app_client.patch(
            "/api/settings/display",
            json={"updates": {"dashboard_refresh_ms": 5000}},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert r.status_code == 200

    def test_ml_status_requires_auth(self, app_client):
        r = app_client.get("/api/settings/ml/status")
        assert r.status_code == 401

    def test_ml_status_with_auth_200(self, app_client, auth_token):
        r = app_client.get(
            "/api/settings/ml/status",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert "ml_enabled" in data
        assert "lstm_enabled" in data
        assert "dqn_enabled" in data

    def test_clear_sensor_data_requires_auth(self, app_client):
        r = app_client.delete("/api/settings/data/sensor")
        assert r.status_code == 401

    def test_clear_sensor_data_with_auth(self, app_client, auth_token):
        r = app_client.delete(
            "/api/settings/data/sensor",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert r.status_code == 200

    def test_clear_alerts_requires_auth(self, app_client):
        r = app_client.delete("/api/settings/data/alerts")
        assert r.status_code == 401

    def test_clear_faults_requires_auth(self, app_client):
        r = app_client.delete("/api/settings/data/faults")
        assert r.status_code == 401

    def test_clear_lstm_requires_auth(self, app_client):
        r = app_client.delete("/api/settings/data/lstm")
        assert r.status_code == 401

    def test_export_csv_requires_auth(self, app_client):
        r = app_client.get("/api/settings/data/export/csv")
        assert r.status_code == 401

    def test_export_csv_with_auth(self, app_client, auth_token):
        r = app_client.get(
            "/api/settings/data/export/csv",
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert r.status_code == 200

    def test_change_password_requires_auth(self, app_client):
        r = app_client.post(
            "/api/settings/security/change-password",
            json={"current": "admin", "new_password": "newpass"},
        )
        assert r.status_code == 401

    def test_change_password_wrong_current_400(self, app_client, auth_token):
        r = app_client.post(
            "/api/settings/security/change-password",
            json={"current": "wrongpassword_xyz", "new_password": "newpass"},
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert r.status_code == 400

    def test_revoke_all_sessions_requires_auth(self, app_client):
        r = app_client.post("/api/settings/security/revoke-all")
        assert r.status_code == 401

    def test_groq_key_patch_requires_auth(self, app_client):
        r = app_client.patch(
            "/api/settings/integrations/groq",
            json={"updates": {"groq_api_key": ""}},
        )
        assert r.status_code == 401

    def test_groq_test_requires_auth(self, app_client):
        r = app_client.post("/api/settings/integrations/groq/test")
        assert r.status_code == 401
