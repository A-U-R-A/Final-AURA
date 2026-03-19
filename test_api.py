"""
AURA API Test Suite
Tests all REST API endpoints and WebSocket connectivity.
"""

import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

import constants

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

VALID_LOCATION = constants.LOCATIONS[0]       # "JLP & JPM"
INVALID_LOCATION = "Nonexistent Module"
VALID_FAULT = list(constants.FAULT_IMPACT_SEVERITY.keys())[0]   # "Cabin Leak"
INVALID_FAULT = "Made Up Fault"
VALID_PARAMETER = "O2 partial pressure"


async def _noop_loop():
    """Replace the background data-generation loop so tests run without ticks."""
    pass


@pytest.fixture(scope="module")
def client():
    """Create a TestClient with the generation loop patched out."""
    with patch("main._generation_loop", _noop_loop):
        with TestClient(__import__("main").app) as c:
            yield c


@pytest.fixture(autouse=True)
def clean_state(client):
    """Clear all faults and data before every test for a clean slate."""
    client.delete("/api/faults")
    client.delete("/api/data")
    yield


# ---------------------------------------------------------------------------
# Health / static
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_200(self, client):
        r = client.get("/health")
        assert r.status_code == 200

    def test_health_has_required_keys(self, client):
        data = client.get("/health").json()
        for key in ("status", "ml_enabled", "lstm_enabled", "dqn_enabled",
                    "db_rows", "unacked_alerts", "ws_clients"):
            assert key in data, f"Missing key: {key}"

    def test_health_status_nominal(self, client):
        assert client.get("/health").json()["status"] == "nominal"

    def test_root_serves_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

class TestConfig:
    def test_config_returns_200(self, client):
        assert client.get("/api/config").status_code == 200

    def test_config_has_required_keys(self, client):
        data = client.get("/api/config").json()
        for key in ("locations", "faults", "actions", "ml_enabled",
                    "lstm_enabled", "dqn_enabled", "parameter_nominal_ranges",
                    "parameter_units", "subsystem_parameters",
                    "fault_precursor_hours"):
            assert key in data, f"Missing key: {key}"

    def test_config_locations_match_constants(self, client):
        data = client.get("/api/config").json()
        assert data["locations"] == constants.LOCATIONS

    def test_config_faults_match_constants(self, client):
        data = client.get("/api/config").json()
        assert set(data["faults"]) == set(constants.FAULT_IMPACT_SEVERITY.keys())

    def test_config_actions_match_constants(self, client):
        data = client.get("/api/config").json()
        assert data["actions"] == constants.ACTIONS_TO_TAKE


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

class TestLocations:
    def test_get_locations_returns_200(self, client):
        assert client.get("/api/locations").status_code == 200

    def test_get_locations_returns_dict(self, client):
        data = client.get("/api/locations").json()
        assert isinstance(data, dict)

    def test_get_locations_count(self, client):
        data = client.get("/api/locations").json()
        assert len(data) == len(constants.LOCATIONS)


# ---------------------------------------------------------------------------
# Location: latest reading
# ---------------------------------------------------------------------------

class TestLatestReading:
    def test_valid_location_returns_200(self, client):
        assert client.get(f"/api/location/{VALID_LOCATION}/latest").status_code == 200

    def test_invalid_location_returns_404(self, client):
        r = client.get(f"/api/location/{INVALID_LOCATION}/latest")
        assert r.status_code == 404

    def test_404_detail_mentions_location(self, client):
        r = client.get(f"/api/location/{INVALID_LOCATION}/latest")
        assert INVALID_LOCATION in r.json().get("detail", "")


# ---------------------------------------------------------------------------
# Location: recent readings
# ---------------------------------------------------------------------------

class TestReadings:
    def test_valid_location_returns_200(self, client):
        assert client.get(f"/api/location/{VALID_LOCATION}/readings").status_code == 200

    def test_invalid_location_returns_404(self, client):
        assert client.get(f"/api/location/{INVALID_LOCATION}/readings").status_code == 404

    def test_n_param_limits_results(self, client):
        r = client.get(f"/api/location/{VALID_LOCATION}/readings?n=5")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) <= 5

    def test_returns_list(self, client):
        data = client.get(f"/api/location/{VALID_LOCATION}/readings").json()
        assert isinstance(data, list)


# ---------------------------------------------------------------------------
# Location: parameter history
# ---------------------------------------------------------------------------

class TestHistory:
    def test_valid_returns_200(self, client):
        url = f"/api/location/{VALID_LOCATION}/history/{VALID_PARAMETER}"
        assert client.get(url).status_code == 200

    def test_invalid_location_returns_404(self, client):
        url = f"/api/location/{INVALID_LOCATION}/history/{VALID_PARAMETER}"
        assert client.get(url).status_code == 404

    def test_returns_list(self, client):
        url = f"/api/location/{VALID_LOCATION}/history/{VALID_PARAMETER}"
        data = client.get(url).json()
        assert isinstance(data, list)

    def test_n_param_respected(self, client):
        url = f"/api/location/{VALID_LOCATION}/history/{VALID_PARAMETER}?n=10"
        data = client.get(url).json()
        assert len(data) <= 10


# ---------------------------------------------------------------------------
# Subsystems
# ---------------------------------------------------------------------------

class TestSubsystems:
    def test_returns_200(self, client):
        assert client.get("/api/subsystems").status_code == 200

    def test_returns_dict(self, client):
        data = client.get("/api/subsystems").json()
        assert isinstance(data, dict)

    def test_contains_known_subsystem(self, client):
        data = client.get("/api/subsystems").json()
        assert "Atmosphere Revitalization System" in data


# ---------------------------------------------------------------------------
# Fault injection
# ---------------------------------------------------------------------------

class TestFaultInject:
    def test_valid_inject_returns_200(self, client):
        r = client.post("/api/faults/inject",
                        json={"location": VALID_LOCATION, "fault": VALID_FAULT})
        assert r.status_code == 200

    def test_valid_inject_response_shape(self, client):
        r = client.post("/api/faults/inject",
                        json={"location": VALID_LOCATION, "fault": VALID_FAULT})
        data = r.json()
        assert data["status"] == "ok"
        assert data["location"] == VALID_LOCATION
        assert data["fault"] == VALID_FAULT

    def test_invalid_location_returns_404(self, client):
        r = client.post("/api/faults/inject",
                        json={"location": INVALID_LOCATION, "fault": VALID_FAULT})
        assert r.status_code == 404

    def test_invalid_fault_returns_400(self, client):
        r = client.post("/api/faults/inject",
                        json={"location": VALID_LOCATION, "fault": INVALID_FAULT})
        assert r.status_code == 400

    def test_missing_body_returns_422(self, client):
        assert client.post("/api/faults/inject", json={}).status_code == 422

    @pytest.mark.parametrize("fault", list(constants.FAULT_IMPACT_SEVERITY.keys()))
    def test_all_faults_injectable(self, client, fault):
        r = client.post("/api/faults/inject",
                        json={"location": VALID_LOCATION, "fault": fault})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Fault clearing
# ---------------------------------------------------------------------------

class TestFaultClearing:
    def test_clear_all_faults_returns_200(self, client):
        assert client.delete("/api/faults").status_code == 200

    def test_clear_all_faults_status_ok(self, client):
        assert client.delete("/api/faults").json()["status"] == "ok"

    def test_clear_location_fault_valid(self, client):
        # Inject first so there's something to clear
        client.post("/api/faults/inject",
                    json={"location": VALID_LOCATION, "fault": VALID_FAULT})
        r = client.delete(f"/api/faults/{VALID_LOCATION}")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_clear_location_fault_invalid(self, client):
        assert client.delete(f"/api/faults/{INVALID_LOCATION}").status_code == 404

    def test_resolve_latched_fault_valid(self, client):
        r = client.delete(f"/api/faults/latch/{VALID_LOCATION}")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_resolve_latched_fault_invalid(self, client):
        assert client.delete(f"/api/faults/latch/{INVALID_LOCATION}").status_code == 404


# ---------------------------------------------------------------------------
# Data clearing
# ---------------------------------------------------------------------------

class TestDataClearing:
    def test_clear_data_returns_200(self, client):
        assert client.delete("/api/data").status_code == 200

    def test_clear_data_has_status_ok(self, client):
        data = client.delete("/api/data").json()
        assert data["status"] == "ok"

    def test_clear_data_has_message(self, client):
        data = client.delete("/api/data").json()
        assert "message" in data


# ---------------------------------------------------------------------------
# LSTM prediction
# ---------------------------------------------------------------------------

class TestPrediction:
    def test_valid_location_returns_200(self, client):
        r = client.get(f"/api/location/{VALID_LOCATION}/prediction")
        assert r.status_code == 200

    def test_invalid_location_returns_404(self, client):
        r = client.get(f"/api/location/{INVALID_LOCATION}/prediction")
        assert r.status_code == 404

    def test_response_has_required_keys(self, client):
        data = client.get(f"/api/location/{VALID_LOCATION}/prediction").json()
        for key in ("location", "lstm_enabled", "ready"):
            assert key in data, f"Missing key: {key}"

    def test_response_location_matches(self, client):
        data = client.get(f"/api/location/{VALID_LOCATION}/prediction").json()
        assert data["location"] == VALID_LOCATION

    def test_not_ready_includes_buffer_info(self, client):
        data = client.get(f"/api/location/{VALID_LOCATION}/prediction").json()
        # When LSTM buffer hasn't filled yet, ready=False should include buffer_fill
        if not data.get("ready"):
            assert "buffer_fill" in data
            assert "seq_len" in data


# ---------------------------------------------------------------------------
# DQN recommendation
# ---------------------------------------------------------------------------

class TestRecommendation:
    def test_valid_location_returns_200(self, client):
        r = client.get(f"/api/location/{VALID_LOCATION}/recommendation")
        assert r.status_code == 200

    def test_invalid_location_returns_404(self, client):
        r = client.get(f"/api/location/{INVALID_LOCATION}/recommendation")
        assert r.status_code == 404

    def test_response_has_required_keys(self, client):
        data = client.get(f"/api/location/{VALID_LOCATION}/recommendation").json()
        for key in ("location", "dqn_enabled", "action"):
            assert key in data, f"Missing key: {key}"

    def test_response_location_matches(self, client):
        data = client.get(f"/api/location/{VALID_LOCATION}/recommendation").json()
        assert data["location"] == VALID_LOCATION

    def test_action_is_valid(self, client):
        data = client.get(f"/api/location/{VALID_LOCATION}/recommendation").json()
        # Either a known action or the default "No Action Needed" when no data
        if data.get("ready"):
            assert data["action"] in constants.ACTIONS_TO_TAKE


# ---------------------------------------------------------------------------
# Trend detection
# ---------------------------------------------------------------------------

class TestTrends:
    def test_valid_location_returns_200(self, client):
        r = client.get(f"/api/location/{VALID_LOCATION}/trends")
        assert r.status_code == 200

    def test_invalid_location_returns_404(self, client):
        r = client.get(f"/api/location/{INVALID_LOCATION}/trends")
        assert r.status_code == 404

    def test_response_has_required_keys(self, client):
        data = client.get(f"/api/location/{VALID_LOCATION}/trends").json()
        for key in ("location", "n_readings", "trends"):
            assert key in data, f"Missing key: {key}"

    def test_response_location_matches(self, client):
        data = client.get(f"/api/location/{VALID_LOCATION}/trends").json()
        assert data["location"] == VALID_LOCATION

    def test_n_param_reflected_in_response(self, client):
        r = client.get(f"/api/location/{VALID_LOCATION}/trends?n=25")
        assert r.json()["n_readings"] == 25


# ---------------------------------------------------------------------------
# Maintenance schedule
# ---------------------------------------------------------------------------

class TestMaintenance:
    def test_returns_200(self, client):
        assert client.get("/api/maintenance").status_code == 200

    def test_response_has_required_keys(self, client):
        data = client.get("/api/maintenance").json()
        for key in ("mission_elapsed_hours", "mission_elapsed_days",
                    "replacement_schedule", "calibration_schedule"):
            assert key in data, f"Missing key: {key}"

    def test_replacement_schedule_is_list(self, client):
        data = client.get("/api/maintenance").json()
        assert isinstance(data["replacement_schedule"], list)

    def test_calibration_schedule_is_list(self, client):
        data = client.get("/api/maintenance").json()
        assert isinstance(data["calibration_schedule"], list)

    def test_replacement_entries_have_required_fields(self, client):
        data = client.get("/api/maintenance").json()
        for entry in data["replacement_schedule"]:
            for field in ("subsystem", "mtbf_hours", "elapsed_hours",
                          "remaining_hours", "pct_life_used", "status"):
                assert field in entry, f"Missing field '{field}' in entry"

    def test_replacement_schedule_sorted_by_pct_life_used(self, client):
        entries = client.get("/api/maintenance").json()["replacement_schedule"]
        pcts = [e["pct_life_used"] for e in entries]
        assert pcts == sorted(pcts, reverse=True)


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

class TestAlerts:
    def test_get_alerts_returns_200(self, client):
        assert client.get("/api/alerts").status_code == 200

    def test_get_alerts_returns_list(self, client):
        assert isinstance(client.get("/api/alerts").json(), list)

    def test_limit_param(self, client):
        r = client.get("/api/alerts?limit=5")
        assert r.status_code == 200
        assert len(r.json()) <= 5

    def test_filter_by_location(self, client):
        r = client.get(f"/api/alerts?location={VALID_LOCATION}")
        assert r.status_code == 200
        data = r.json()
        for alert in data:
            assert alert.get("location_name") == VALID_LOCATION

    def test_unacked_only_filter(self, client):
        r = client.get("/api/alerts?unacked_only=true")
        assert r.status_code == 200
        for alert in r.json():
            assert not alert.get("acknowledged")

    def test_get_alert_count_returns_200(self, client):
        assert client.get("/api/alerts/count").status_code == 200

    def test_alert_count_has_required_keys(self, client):
        data = client.get("/api/alerts/count").json()
        assert "unacknowledged" in data
        assert "total" in data

    def test_alert_count_total_gte_unacknowledged(self, client):
        data = client.get("/api/alerts/count").json()
        assert data["total"] >= data["unacknowledged"]

    def test_acknowledge_nonexistent_alert(self, client):
        # Should not crash — DB silently ignores missing IDs
        r = client.post("/api/alerts/99999/acknowledge")
        assert r.status_code == 200

    def test_acknowledge_alert_response(self, client):
        r = client.post("/api/alerts/1/acknowledge")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_acknowledge_all_returns_200(self, client):
        assert client.post("/api/alerts/acknowledge-all").status_code == 200

    def test_acknowledge_all_response(self, client):
        assert client.post("/api/alerts/acknowledge-all").json()["status"] == "ok"

    def test_unacked_count_zero_after_acknowledge_all(self, client):
        client.post("/api/alerts/acknowledge-all")
        data = client.get("/api/alerts/count").json()
        assert data["unacknowledged"] == 0


# ---------------------------------------------------------------------------
# AI Analysis
# ---------------------------------------------------------------------------

class TestAIAnalysis:
    def test_invalid_location_returns_404(self, client):
        r = client.post("/api/ai/analyze",
                        json={"location": INVALID_LOCATION})
        assert r.status_code == 404

    def test_missing_body_returns_422(self, client):
        assert client.post("/api/ai/analyze", json={}).status_code == 422

    def test_valid_location_returns_200(self, client):
        with patch("ai_analyst.analyze", return_value="Mocked analysis response."):
            r = client.post("/api/ai/analyze",
                            json={"location": VALID_LOCATION, "model": "mistral"})
        assert r.status_code == 200

    def test_response_has_required_keys(self, client):
        with patch("ai_analyst.analyze", return_value="Mocked analysis response."):
            data = client.post("/api/ai/analyze",
                               json={"location": VALID_LOCATION}).json()
        assert "location" in data
        assert "response" in data

    def test_response_location_matches(self, client):
        with patch("ai_analyst.analyze", return_value="Test."):
            data = client.post("/api/ai/analyze",
                               json={"location": VALID_LOCATION}).json()
        assert data["location"] == VALID_LOCATION

    def test_default_model_is_mistral(self, client):
        captured = {}

        def capture_analyze(location, readings, model):
            captured["model"] = model
            return "ok"

        with patch("ai_analyst.analyze", side_effect=capture_analyze):
            client.post("/api/ai/analyze", json={"location": VALID_LOCATION})
        assert captured.get("model") == "mistral"

    def test_custom_model_forwarded(self, client):
        captured = {}

        def capture_analyze(location, readings, model):
            captured["model"] = model
            return "ok"

        with patch("ai_analyst.analyze", side_effect=capture_analyze):
            client.post("/api/ai/analyze",
                        json={"location": VALID_LOCATION, "model": "llama3"})
        assert captured.get("model") == "llama3"

    @pytest.mark.parametrize("location", constants.LOCATIONS)
    def test_all_locations_accepted(self, client, location):
        with patch("ai_analyst.analyze", return_value="ok"):
            r = client.post("/api/ai/analyze", json={"location": location})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

class TestWebSocket:
    def test_websocket_connects(self, client):
        with client.websocket_connect("/ws/live") as ws:
            msg = ws.receive_json()
            assert msg is not None

    def test_initial_message_is_state(self, client):
        with client.websocket_connect("/ws/live") as ws:
            msg = ws.receive_json()
            assert msg.get("type") == "state"

    def test_initial_state_has_locations(self, client):
        with client.websocket_connect("/ws/live") as ws:
            msg = ws.receive_json()
            assert "locations" in msg

    def test_initial_state_has_timestamp(self, client):
        with client.websocket_connect("/ws/live") as ws:
            msg = ws.receive_json()
            assert "timestamp" in msg


# ---------------------------------------------------------------------------
# Multi-location parametric
# ---------------------------------------------------------------------------

class TestAllLocations:
    @pytest.mark.parametrize("location", constants.LOCATIONS)
    def test_latest_for_all_locations(self, client, location):
        assert client.get(f"/api/location/{location}/latest").status_code == 200

    @pytest.mark.parametrize("location", constants.LOCATIONS)
    def test_prediction_for_all_locations(self, client, location):
        assert client.get(f"/api/location/{location}/prediction").status_code == 200

    @pytest.mark.parametrize("location", constants.LOCATIONS)
    def test_recommendation_for_all_locations(self, client, location):
        assert client.get(f"/api/location/{location}/recommendation").status_code == 200

    @pytest.mark.parametrize("location", constants.LOCATIONS)
    def test_trends_for_all_locations(self, client, location):
        assert client.get(f"/api/location/{location}/trends").status_code == 200

    @pytest.mark.parametrize("location", constants.LOCATIONS)
    def test_clear_fault_for_all_locations(self, client, location):
        assert client.delete(f"/api/faults/{location}").status_code == 200

    @pytest.mark.parametrize("location", constants.LOCATIONS)
    def test_resolve_latch_for_all_locations(self, client, location):
        assert client.delete(f"/api/faults/latch/{location}").status_code == 200
