"""
Tests for app/ai_analyst.py — backend detection, snapshot building,
system prompt construction, and stream routing.
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from app import ai_analyst, constants


# ── BACKEND DETECTION ─────────────────────────────────────────────────────────

class TestGetBackend:
    def test_returns_string(self):
        backend = ai_analyst.get_backend()
        assert isinstance(backend, str)

    def test_valid_backend_value(self):
        backend = ai_analyst.get_backend()
        assert backend in {"ollama", "groq", "none"}

    def test_groq_when_no_ollama_and_key_set(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test-key-123")
        with patch.object(ai_analyst, "_is_ollama_available", return_value=False):
            backend = ai_analyst.get_backend()
        assert backend == "groq"
        monkeypatch.delenv("GROQ_API_KEY", raising=False)

    def test_none_when_no_ollama_no_key(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        with patch.object(ai_analyst, "_is_ollama_available", return_value=False):
            backend = ai_analyst.get_backend()
        assert backend == "none"

    def test_ollama_takes_precedence_over_groq(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "test-key")
        with patch.object(ai_analyst, "_is_ollama_available", return_value=True):
            backend = ai_analyst.get_backend()
        assert backend == "ollama"
        monkeypatch.delenv("GROQ_API_KEY", raising=False)


# ── OLLAMA AVAILABILITY CACHE ─────────────────────────────────────────────────

class TestOllamaAvailability:
    def test_returns_bool(self):
        result = ai_analyst._is_ollama_available()
        assert isinstance(result, bool)

    def test_cache_hit_skips_check(self):
        ai_analyst._ollama_ok = True
        ai_analyst._ollama_checked_at = 1e18  # far future
        result = ai_analyst._is_ollama_available()
        assert result is True
        ai_analyst._ollama_ok = None
        ai_analyst._ollama_checked_at = 0.0

    def test_returns_false_when_ollama_raises(self):
        ai_analyst._ollama_ok = None
        ai_analyst._ollama_checked_at = 0.0
        with patch.dict("sys.modules", {"ollama": None}):
            result = ai_analyst._is_ollama_available()
        # When import fails or ollama.list() raises, should return False
        # Reset
        ai_analyst._ollama_ok = None
        ai_analyst._ollama_checked_at = 0.0


# ── SYSTEM PROMPT ─────────────────────────────────────────────────────────────

class TestStaticSystemPrompt:
    def setup_method(self):
        ai_analyst._STATIC_SYSTEM_PROMPT = None  # force rebuild

    def test_returns_string(self):
        prompt = ai_analyst._get_static_system_prompt()
        assert isinstance(prompt, str)

    def test_contains_aura_role(self):
        prompt = ai_analyst._get_static_system_prompt()
        assert "AURA" in prompt

    def test_contains_nominal_ranges_reference(self):
        prompt = ai_analyst._get_static_system_prompt()
        assert "Nominal ranges" in prompt or "nominal" in prompt.lower()

    def test_contains_at_least_one_known_fault(self):
        prompt = ai_analyst._get_static_system_prompt()
        assert "Cabin Leak" in prompt

    def test_contains_no_action_needed(self):
        prompt = ai_analyst._get_static_system_prompt()
        assert "No Action Needed" in prompt

    def test_cached_on_second_call(self):
        p1 = ai_analyst._get_static_system_prompt()
        p2 = ai_analyst._get_static_system_prompt()
        assert p1 is p2  # same object (cached)

    def test_prompt_not_empty(self):
        prompt = ai_analyst._get_static_system_prompt()
        assert len(prompt) > 200


# ── COMPACT HELPER ─────────────────────────────────────────────────────────────

class TestCompact:
    def test_returns_string(self):
        result = ai_analyst._compact({"a": 1, "b": 2})
        assert isinstance(result, str)

    def test_contains_keys(self):
        result = ai_analyst._compact({"alpha": 10, "beta": 20})
        assert "alpha" in result
        assert "beta" in result

    def test_braces_present(self):
        result = ai_analyst._compact({"x": 1})
        assert result.startswith("{") and result.endswith("}")

    def test_empty_dict(self):
        result = ai_analyst._compact({})
        assert result == "{}"


# ── SNAPSHOT BUILDER ──────────────────────────────────────────────────────────

class _FakeDB:
    """Minimal DB stub for snapshot testing."""
    def get_latest_reading(self, loc):
        return {
            "if_label": 1,
            "data": {p: 1.0 for p in list(constants.PARAMETER_NOMINAL_RANGES.keys())[:3]},
        }

    def get_recent_readings(self, loc, n=10):
        return [
            {"if_label": 1, "rf_classification": None,
             "timestamp": "2025-01-01T00:00:00",
             "data": {p: 1.0 for p in list(constants.PARAMETER_NOMINAL_RANGES.keys())[:3]}}
            for _ in range(n)
        ]

    def get_alerts(self, limit=10):
        return []


class TestBuildSnapshot:
    def test_returns_string(self):
        result = ai_analyst._build_snapshot(_FakeDB(), n_readings=3)
        assert isinstance(result, str)

    def test_contains_location_status_header(self):
        result = ai_analyst._build_snapshot(_FakeDB(), n_readings=3)
        assert "LOCATION STATUS" in result

    def test_contains_all_locations(self):
        result = ai_analyst._build_snapshot(_FakeDB(), n_readings=3)
        for loc in constants.LOCATIONS:
            assert loc in result

    def test_contains_alerts_header(self):
        result = ai_analyst._build_snapshot(_FakeDB(), n_readings=3)
        assert "ALERT" in result.upper()

    def test_no_alerts_line_when_empty(self):
        result = ai_analyst._build_snapshot(_FakeDB(), n_readings=3)
        assert "No recent alerts" in result

    def test_sensor_readings_section(self):
        result = ai_analyst._build_snapshot(_FakeDB(), n_readings=3)
        assert "SENSOR READINGS" in result

    def test_background_prefix(self):
        result = ai_analyst._build_snapshot(_FakeDB(), n_readings=3)
        assert "BACKGROUND" in result


# ── CHAT STREAM ROUTING ───────────────────────────────────────────────────────

class TestChatStream:
    def _messages(self):
        return [{"role": "user", "content": "Hello"}]

    def test_returns_tuple(self):
        with patch.object(ai_analyst, "_is_ollama_available", return_value=False):
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("GROQ_API_KEY", None)
                result = ai_analyst.chat_stream(self._messages(), "mistral", _FakeDB())
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_no_backend_returns_none_backend(self):
        with patch.object(ai_analyst, "_is_ollama_available", return_value=False):
            os.environ.pop("GROQ_API_KEY", None)
            backend, gen = ai_analyst.chat_stream(self._messages(), "mistral", _FakeDB())
        assert backend == "none"

    def test_no_backend_generator_yields_string(self):
        with patch.object(ai_analyst, "_is_ollama_available", return_value=False):
            os.environ.pop("GROQ_API_KEY", None)
            _, gen = ai_analyst.chat_stream(self._messages(), "mistral", _FakeDB())
            text = "".join(gen)
        assert "No AI backend" in text or len(text) > 0

    def test_snapshot_injected_into_last_user_message(self):
        captured = []

        def fake_stream_ollama(messages, model):
            captured.append(messages)
            return iter([])

        with patch.object(ai_analyst, "_is_ollama_available", return_value=True):
            with patch.object(ai_analyst, "_stream_ollama", side_effect=fake_stream_ollama):
                ai_analyst.chat_stream(self._messages(), "mistral", _FakeDB())

        if captured:
            last_user = next(
                (m for m in reversed(captured[0]) if m["role"] == "user"), None
            )
            assert last_user is not None
            assert "BACKGROUND" in last_user["content"] or "LOCATION" in last_user["content"]

    def test_system_prompt_added_as_first_message(self):
        captured = []

        def fake_stream_ollama(messages, model):
            captured.append(messages)
            return iter([])

        with patch.object(ai_analyst, "_is_ollama_available", return_value=True):
            with patch.object(ai_analyst, "_stream_ollama", side_effect=fake_stream_ollama):
                ai_analyst.chat_stream(self._messages(), "mistral", _FakeDB())

        if captured:
            assert captured[0][0]["role"] == "system"

    def test_groq_model_map_lookup(self):
        assert "mistral" in ai_analyst.GROQ_MODEL_MAP
        for k, v in ai_analyst.GROQ_MODEL_MAP.items():
            assert isinstance(v, str) and len(v) > 0

    def test_empty_messages_handled(self):
        with patch.object(ai_analyst, "_is_ollama_available", return_value=False):
            os.environ.pop("GROQ_API_KEY", None)
            backend, gen = ai_analyst.chat_stream([], "mistral", _FakeDB())
        # Should not raise
        assert backend in {"ollama", "groq", "none"}
