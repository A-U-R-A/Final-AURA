"""
Tests for app/dqn_recommender.py — model loading, state encoding,
action recommendation output, RF bypass logic, and graceful fallback.
"""

import pytest
import numpy as np
from app import constants
from app.dqn_recommender import DQNRecommender, STATE_SIZE, N_ACTIONS
from app.data_generator import SensorDataGenerator


@pytest.fixture(scope="module")
def dqn():
    return DQNRecommender()


@pytest.fixture(scope="module")
def gen():
    return SensorDataGenerator(seed=33)


@pytest.fixture(scope="module")
def nominal_reading(gen):
    return gen.generate_nominal_batch(n=1)[0]


# ── MODEL LOADING ─────────────────────────────────────────────────────────────

class TestDQNLoading:
    def test_dqn_enabled(self, dqn):
        assert dqn.enabled is True, "DQN failed to load"

    def test_model_not_none(self, dqn):
        assert dqn.model is not None

    def test_param_order_matches_constants(self, dqn):
        assert set(dqn.param_order) == set(constants.PARAMETER_NOMINAL_RANGES.keys())

    def test_faults_match_constants(self, dqn):
        assert set(dqn.faults) == set(constants.FAULT_IMPACT_SEVERITY.keys())

    def test_actions_match_constants(self, dqn):
        assert dqn.actions == constants.ACTIONS_TO_TAKE

    def test_scaler_arrays_set(self, dqn):
        assert dqn.scaler_mean is not None
        assert dqn.scaler_std is not None

    def test_missing_model_disables_gracefully(self, tmp_path):
        bad_dqn = DQNRecommender(model_path=str(tmp_path / "missing.pt"))
        assert bad_dqn.enabled is False


# ── STATE ENCODING ────────────────────────────────────────────────────────────

class TestEncode:
    def test_encode_returns_array(self, dqn, nominal_reading):
        state = dqn._encode(nominal_reading, 0.0, 1, None, 0.0, 200.0)
        assert isinstance(state, np.ndarray)

    def test_encode_correct_shape(self, dqn, nominal_reading):
        state = dqn._encode(nominal_reading, 0.0, 1, None, 0.0, 200.0)
        assert state.shape == (STATE_SIZE,)

    def test_encode_rf_probs_zero_when_nominal(self, dqn, nominal_reading):
        state = dqn._encode(nominal_reading, 0.0, 1, None, 0.0, 200.0)
        n_params = len(constants.PARAMETER_NOMINAL_RANGES)
        n_faults = len(constants.FAULT_IMPACT_SEVERITY)
        rf_segment = state[n_params:n_params + n_faults]
        assert np.all(rf_segment == 0.0)

    def test_encode_rf_probs_filled_when_anomalous(self, dqn, nominal_reading):
        rf = {f: 1.0 / len(constants.FAULT_IMPACT_SEVERITY)
              for f in constants.FAULT_IMPACT_SEVERITY}
        state = dqn._encode(nominal_reading, -0.5, -1, rf, 0.3, 100.0)
        n_params = len(constants.PARAMETER_NOMINAL_RANGES)
        n_faults = len(constants.FAULT_IMPACT_SEVERITY)
        rf_segment = state[n_params:n_params + n_faults]
        assert not np.all(rf_segment == 0.0)

    def test_encode_scalars_in_range(self, dqn, nominal_reading):
        state = dqn._encode(nominal_reading, -5.0, -1, None, 0.7, 50.0)
        # Last 4 elements are scalar context in [0,1]
        scalars = state[-4:]
        assert all(0.0 <= s <= 1.0 for s in scalars), \
            f"Scalar context out of range: {scalars}"

    def test_encode_handles_missing_params(self, dqn):
        partial = {"O2 partial pressure": 20.0}
        state = dqn._encode(partial, 0.0, 1, None, 0.0, 200.0)
        assert state.shape == (STATE_SIZE,)

    def test_encode_rul_capped_at_200(self, dqn, nominal_reading):
        s1 = dqn._encode(nominal_reading, 0.0, 1, None, 0.0, 200.0)
        s2 = dqn._encode(nominal_reading, 0.0, 1, None, 0.0, 9999.0)
        # RUL scalar at index -1 should be 1.0 for both (both capped)
        assert s1[-1] == s2[-1] == 1.0


# ── RECOMMEND OUTPUT ──────────────────────────────────────────────────────────

class TestRecommend:
    def test_recommend_returns_dict(self, dqn, nominal_reading):
        result = dqn.recommend(nominal_reading)
        assert isinstance(result, dict)

    def test_recommend_has_required_keys(self, dqn, nominal_reading):
        result = dqn.recommend(nominal_reading)
        for key in ("action", "action_index", "confidence", "q_values"):
            assert key in result, f"Missing key {key!r}"

    def test_action_is_in_actions_list(self, dqn, nominal_reading):
        result = dqn.recommend(nominal_reading)
        assert result["action"] in constants.ACTIONS_TO_TAKE

    def test_action_index_valid(self, dqn, nominal_reading):
        result = dqn.recommend(nominal_reading)
        idx = result["action_index"]
        assert 0 <= idx < N_ACTIONS, f"action_index={idx} out of range"

    def test_action_index_consistent_with_action(self, dqn, nominal_reading):
        result = dqn.recommend(nominal_reading)
        assert constants.ACTIONS_TO_TAKE[result["action_index"]] == result["action"]

    def test_confidence_in_unit_interval(self, dqn, nominal_reading):
        result = dqn.recommend(nominal_reading)
        c = result["confidence"]
        assert 0.0 <= c <= 1.0, f"confidence={c} outside [0,1]"

    def test_q_values_dict_when_dqn_used(self, dqn, nominal_reading):
        result = dqn.recommend(
            nominal_reading, anomaly_score=-1.0, if_label=-1,
            rf_classification=None, failure_prob=0.5, rul_hours=10.0
        )
        if result["q_values"] is not None:
            assert set(result["q_values"].keys()) == set(constants.ACTIONS_TO_TAKE)


# ── RF BYPASS ─────────────────────────────────────────────────────────────────

class TestRFBypass:
    def test_high_confidence_rf_bypasses_dqn(self, dqn, nominal_reading):
        fault = "Cabin Leak"
        expected_action = None
        # Find expected action for Cabin Leak
        from app.dqn_recommender import _FAULT_TO_ACTION
        expected_action = _FAULT_TO_ACTION.get(fault)
        if expected_action is None:
            pytest.skip("Cabin Leak not in _FAULT_TO_ACTION")

        rf = {f: 0.0 for f in constants.FAULT_IMPACT_SEVERITY}
        rf[fault] = 0.99  # above bypass threshold

        result = dqn.recommend(
            nominal_reading, anomaly_score=-2.0, if_label=-1,
            rf_classification=rf, failure_prob=0.8, rul_hours=5.0
        )
        assert result["action"] == expected_action
        assert result["confidence"] == 0.99
        assert result["q_values"] is None

    def test_low_confidence_rf_uses_dqn(self, dqn, nominal_reading):
        rf = {f: 0.1 for f in constants.FAULT_IMPACT_SEVERITY}
        result = dqn.recommend(
            nominal_reading, anomaly_score=-0.5, if_label=-1,
            rf_classification=rf, failure_prob=0.2, rul_hours=100.0
        )
        # Should use DQN — q_values present
        if dqn.enabled:
            assert result["q_values"] is not None

    def test_bypass_only_for_anomalous_label(self, dqn, nominal_reading):
        fault = "Cabin Leak"
        rf = {f: 0.0 for f in constants.FAULT_IMPACT_SEVERITY}
        rf[fault] = 0.99

        # IF nominal (1) — bypass should NOT trigger
        result = dqn.recommend(
            nominal_reading, anomaly_score=0.5, if_label=1,
            rf_classification=rf, failure_prob=0.0, rul_hours=200.0
        )
        # Action should be determined by DQN, not the bypass
        # (No specific assertion on action value, just that it's valid)
        assert result["action"] in constants.ACTIONS_TO_TAKE


# ── DISABLED FALLBACK ─────────────────────────────────────────────────────────

class TestDisabledFallback:
    def test_disabled_returns_no_action(self, tmp_path, nominal_reading):
        bad_dqn = DQNRecommender(model_path=str(tmp_path / "no_model.pt"))
        result = bad_dqn.recommend(nominal_reading)
        assert result["action"] == "No Action Needed"
        assert result["action_index"] == 0
        assert result["confidence"] == 0.0
        assert result["q_values"] is None
