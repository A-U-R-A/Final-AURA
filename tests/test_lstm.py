"""
Tests for app/lstm_predictor.py — model loading, buffer mechanics,
prediction output shape/bounds, and buffer isolation between locations.
"""

import pytest
import numpy as np
from app import constants
from app.lstm_predictor import LSTMPipeline
from app.data_generator import SensorDataGenerator


@pytest.fixture(scope="module")
def lstm():
    return LSTMPipeline()


@pytest.fixture(scope="module")
def gen():
    return SensorDataGenerator(seed=77)


@pytest.fixture(scope="module")
def nominal_batch(gen):
    return gen.generate_nominal_batch(n=200)


# ── MODEL LOADING ─────────────────────────────────────────────────────────────

class TestLSTMLoading:
    def test_lstm_enabled(self, lstm):
        assert lstm.enabled is True, "LSTM failed to load"

    def test_model_not_none(self, lstm):
        assert lstm.model is not None

    def test_param_order_not_empty(self, lstm):
        assert lstm.param_order and len(lstm.param_order) > 0

    def test_scaler_arrays_set(self, lstm):
        assert lstm.scaler_mean is not None
        assert lstm.scaler_std is not None

    def test_scaler_mean_std_same_length(self, lstm):
        assert len(lstm.scaler_mean) == len(lstm.scaler_std)

    def test_seq_len_positive(self, lstm):
        assert lstm.seq_len > 0

    def test_buffers_initialized_for_all_locations(self, lstm):
        for loc in constants.LOCATIONS:
            assert loc in lstm._buffers

    def test_missing_model_disables_gracefully(self, tmp_path):
        bad_lstm = LSTMPipeline(model_path=str(tmp_path / "missing.pt"))
        assert bad_lstm.enabled is False

    def test_disabled_predict_returns_none(self, tmp_path):
        bad_lstm = LSTMPipeline(model_path=str(tmp_path / "missing.pt"))
        result = bad_lstm.predict(constants.LOCATIONS[0])
        assert result is None


# ── BUFFER MECHANICS ──────────────────────────────────────────────────────────

class TestBuffer:
    def test_push_adds_to_buffer(self, lstm, nominal_batch):
        loc = constants.LOCATIONS[-1]
        lstm.clear_buffer(loc)
        lstm.push(loc, nominal_batch[0])
        assert len(lstm._buffers[loc]) == 1

    def test_buffer_does_not_exceed_seq_len(self, lstm, nominal_batch):
        loc = constants.LOCATIONS[-1]
        lstm.clear_buffer(loc)
        for reading in nominal_batch[:lstm.seq_len + 10]:
            lstm.push(loc, reading)
        assert len(lstm._buffers[loc]) == lstm.seq_len

    def test_clear_buffer_single_location(self, lstm, nominal_batch):
        loc = constants.LOCATIONS[0]
        lstm.push(loc, nominal_batch[0])
        lstm.clear_buffer(loc)
        assert len(lstm._buffers[loc]) == 0

    def test_clear_buffer_all(self, lstm, nominal_batch):
        for loc in constants.LOCATIONS[:3]:
            lstm.push(loc, nominal_batch[0])
        lstm.clear_buffer()
        for loc in constants.LOCATIONS:
            assert len(lstm._buffers[loc]) == 0

    def test_clear_buffer_does_not_affect_other_locations(self, lstm, nominal_batch):
        loc1, loc2 = constants.LOCATIONS[0], constants.LOCATIONS[1]
        lstm.clear_buffer()
        lstm.push(loc2, nominal_batch[0])
        lstm.clear_buffer(loc1)  # clear loc1 only
        assert len(lstm._buffers[loc2]) == 1

    def test_buffer_is_fifo(self, lstm, nominal_batch):
        loc = constants.LOCATIONS[0]
        lstm.clear_buffer(loc)
        for r in nominal_batch[:lstm.seq_len + 5]:
            lstm.push(loc, r)
        # The last entry in the buffer should match the last pushed reading
        last_pushed = nominal_batch[lstm.seq_len + 4]
        buf_last = lstm._buffers[loc][-1]
        for key in last_pushed:
            assert abs(buf_last[key] - last_pushed[key]) < 1e-9


# ── PREDICTION OUTPUT ─────────────────────────────────────────────────────────

class TestPredictOutput:
    def _fill_buffer(self, lstm, gen, loc, n=None):
        lstm.clear_buffer(loc)
        batch = gen.generate_nominal_batch(n=n or lstm.seq_len + 5)
        for r in batch[:lstm.seq_len + 2]:
            lstm.push(loc, r)

    def test_predict_none_when_buffer_empty(self, lstm):
        loc = constants.LOCATIONS[0]
        lstm.clear_buffer(loc)
        assert lstm.predict(loc) is None

    def test_predict_none_when_buffer_underfull(self, lstm, nominal_batch):
        loc = constants.LOCATIONS[0]
        lstm.clear_buffer(loc)
        for r in nominal_batch[:lstm.seq_len - 1]:
            lstm.push(loc, r)
        assert lstm.predict(loc) is None

    def test_predict_returns_dict_when_buffer_full(self, lstm, gen):
        loc = constants.LOCATIONS[0]
        self._fill_buffer(lstm, gen, loc)
        result = lstm.predict(loc)
        assert result is not None
        assert isinstance(result, dict)

    def test_predict_has_failure_prob(self, lstm, gen):
        loc = constants.LOCATIONS[0]
        self._fill_buffer(lstm, gen, loc)
        result = lstm.predict(loc)
        assert "failure_prob" in result

    def test_predict_has_rul_hours(self, lstm, gen):
        loc = constants.LOCATIONS[0]
        self._fill_buffer(lstm, gen, loc)
        result = lstm.predict(loc)
        assert "rul_hours" in result

    def test_failure_prob_in_unit_interval(self, lstm, gen):
        loc = constants.LOCATIONS[0]
        self._fill_buffer(lstm, gen, loc)
        result = lstm.predict(loc)
        fp = result["failure_prob"]
        assert 0.0 <= fp <= 1.0, f"failure_prob={fp} outside [0,1]"

    def test_rul_hours_nonnegative(self, lstm, gen):
        loc = constants.LOCATIONS[0]
        self._fill_buffer(lstm, gen, loc)
        result = lstm.predict(loc)
        rul = result["rul_hours"]
        assert rul >= 0.0, f"rul_hours={rul} is negative"

    def test_predict_is_deterministic(self, lstm, gen):
        loc = constants.LOCATIONS[0]
        self._fill_buffer(lstm, gen, loc)
        r1 = lstm.predict(loc)
        r2 = lstm.predict(loc)
        assert r1["failure_prob"] == r2["failure_prob"]
        assert r1["rul_hours"] == r2["rul_hours"]

    def test_fault_buffer_higher_failure_prob(self, lstm, gen):
        """Fault-filled buffer should produce higher failure_prob than nominal."""
        fault = "Cabin Leak"
        gen_fault = SensorDataGenerator(seed=55, step_seconds=3600.0)

        nom_loc  = constants.LOCATIONS[0]
        fault_loc = constants.LOCATIONS[1]

        lstm.clear_buffer(nom_loc)
        lstm.clear_buffer(fault_loc)

        nom_batch = gen.generate_nominal_batch(n=lstm.seq_len + 10)
        fault_readings, _ = gen_fault.generate_fault_batch(fault, n_per_fault=lstm.seq_len + 10)

        for r in nom_batch[:lstm.seq_len]:
            lstm.push(nom_loc, r)
        for r in fault_readings[:lstm.seq_len]:
            lstm.push(fault_loc, r)

        nom_pred   = lstm.predict(nom_loc)
        fault_pred = lstm.predict(fault_loc)

        if nom_pred and fault_pred:
            assert fault_pred["failure_prob"] >= nom_pred["failure_prob"], \
                f"Fault fp {fault_pred['failure_prob']:.3f} not >= nominal {nom_pred['failure_prob']:.3f}"
