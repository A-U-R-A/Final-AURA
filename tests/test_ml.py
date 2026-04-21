"""
Tests for app/ml_pipeline.py — model loading, IF+RF inference,
output shapes, graceful degradation when models are absent.
"""

import warnings
import pytest
import numpy as np
from app import constants
from app.ml_pipeline import MLPipeline
from app.data_generator import SensorDataGenerator

# Detect sklearn version mismatch so performance tests can be skipped.
# Clear per-module warning registries first — otherwise warnings that already fired
# (e.g. when main.py loaded MLPipeline) won't fire again even with simplefilter("always").
import sklearn
import sklearn.base
import sklearn.ensemble
for _mod in (sklearn.base, sklearn.ensemble):
    if hasattr(_mod, "__warningregistry__"):
        _mod.__warningregistry__.clear()

from sklearn.exceptions import InconsistentVersionWarning
_SKLEARN_MISMATCH = False
with warnings.catch_warnings(record=True) as _caught_w:
    warnings.simplefilter("always")
    _test_ml = MLPipeline()
    _SKLEARN_MISMATCH = any(
        issubclass(w.category, InconsistentVersionWarning) for w in _caught_w
    )

skip_if_version_mismatch = pytest.mark.skipif(
    _SKLEARN_MISMATCH,
    reason="sklearn version mismatch between model and runtime — performance not guaranteed"
)


@pytest.fixture(scope="module")
def ml():
    return _test_ml  # reuse the instance already loaded for version detection


@pytest.fixture(scope="module")
def gen():
    return SensorDataGenerator(seed=99)


@pytest.fixture(scope="module")
def nominal_reading(gen):
    return gen.generate_nominal_batch(n=1)[0]


@pytest.fixture(scope="module")
def fault_reading(gen):
    readings, _ = gen.generate_fault_batch("Cabin Leak", n_per_fault=100)
    return readings[-1]


# ── MODEL LOADING ─────────────────────────────────────────────────────────────

class TestModelLoading:
    def test_ml_enabled(self, ml):
        assert ml.enabled is True, "ML pipeline failed to load models"

    def test_if_model_not_none(self, ml):
        assert ml.if_model is not None

    def test_rf_model_not_none(self, ml):
        assert ml.rf_model is not None

    def test_scaler_not_none(self, ml):
        assert ml.scaler is not None

    def test_param_order_matches_constants(self, ml):
        assert set(ml.param_order) == set(constants.PARAMETER_NOMINAL_RANGES.keys())
        assert len(ml.param_order) == len(constants.PARAMETER_NOMINAL_RANGES)

    def test_rf_classes_match_faults(self, ml):
        rf_classes = set(ml.rf_model.classes_)
        expected = set(constants.FAULT_IMPACT_SEVERITY.keys())
        assert rf_classes == expected, \
            f"RF classes {rf_classes} != fault constants {expected}"

    def test_missing_model_disables_gracefully(self, tmp_path):
        bad_ml = MLPipeline(
            if_path=str(tmp_path / "missing_if.joblib"),
            rf_path=str(tmp_path / "missing_rf.joblib"),
        )
        assert bad_ml.enabled is False

    def test_disabled_predict_returns_nominal(self, tmp_path):
        bad_ml = MLPipeline(
            if_path=str(tmp_path / "missing_if.joblib"),
            rf_path=str(tmp_path / "missing_rf.joblib"),
        )
        label, rf_class = bad_ml.predict({"O2 partial pressure": 20.0})
        assert label == 1
        assert rf_class is None


# ── PREDICT OUTPUT ────────────────────────────────────────────────────────────

class TestPredict:
    def test_predict_returns_tuple(self, ml, nominal_reading):
        result = ml.predict(nominal_reading)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_predict_label_in_valid_set(self, ml, nominal_reading):
        label, _ = ml.predict(nominal_reading)
        assert label in {-1, 1}, f"IF label {label!r} not in {{-1, 1}}"

    def test_predict_nominal_mostly_returns_1(self, ml, gen):
        batch = gen.generate_nominal_batch(n=200)
        labels = [ml.predict(r)[0] for r in batch]
        fpr = sum(1 for l in labels if l == -1) / len(labels)
        assert fpr <= 0.10, f"False positive rate {fpr:.1%} too high (>10%)"

    def test_predict_fault_rf_class_dict_when_anomalous(self, ml, fault_reading):
        label, rf_class = ml.predict(fault_reading)
        if label == -1:
            assert isinstance(rf_class, dict)
            assert set(rf_class.keys()) == set(constants.FAULT_IMPACT_SEVERITY.keys())

    def test_predict_rf_probabilities_sum_to_one(self, ml, fault_reading):
        label, rf_class = ml.predict(fault_reading)
        if rf_class is not None:
            total = sum(rf_class.values())
            assert abs(total - 1.0) < 1e-3, f"RF probs sum to {total}"

    def test_predict_rf_none_when_nominal_label(self, ml, gen):
        # When IF returns 1 (nominal), RF should return None
        batch = gen.generate_nominal_batch(n=100)
        for r in batch:
            label, rf_class = ml.predict(r)
            if label == 1:
                assert rf_class is None
                return  # found one nominal prediction
        pytest.skip("No nominal predictions produced (high FPR)")

    def test_predict_handles_missing_params(self, ml):
        partial = {"O2 partial pressure": 20.5}  # only one param
        label, _ = ml.predict(partial)
        assert label in {-1, 1}

    def test_predict_handles_empty_dict(self, ml):
        label, _ = ml.predict({})
        assert label in {-1, 1}

    @skip_if_version_mismatch
    def test_predict_fault_detection_rate(self, ml, gen):
        """True positive rate should be meaningfully above 0."""
        fault = "Cabin Leak"
        readings, _ = gen.generate_fault_batch(fault, n_per_fault=200)
        labels = [ml.predict(r)[0] for r in readings]
        tpr = sum(1 for l in labels if l == -1) / len(labels)
        assert tpr > 0.05, f"TPR {tpr:.1%} is too low"


# ── ANOMALY SCORE ─────────────────────────────────────────────────────────────

class TestAnomalyScore:
    def test_anomaly_score_returns_float(self, ml, nominal_reading):
        score = ml.anomaly_score(nominal_reading)
        assert isinstance(score, float)

    def test_anomaly_score_nominal_positive(self, ml, gen):
        batch = gen.generate_nominal_batch(n=50)
        scores = [ml.anomaly_score(r) for r in batch]
        # Most nominal readings should have positive score (not anomalous)
        pos = sum(1 for s in scores if s > 0)
        assert pos >= len(scores) * 0.5, "Most nominal scores should be positive"

    def test_anomaly_score_disabled_returns_zero(self, tmp_path):
        bad_ml = MLPipeline(
            if_path=str(tmp_path / "nope_if.joblib"),
            rf_path=str(tmp_path / "nope_rf.joblib"),
        )
        assert bad_ml.anomaly_score({"O2 partial pressure": 20.0}) == 0.0

    @skip_if_version_mismatch
    def test_fault_score_lower_than_nominal(self, ml, gen):
        nominal_batch = gen.generate_nominal_batch(n=50)
        fault_readings, _ = gen.generate_fault_batch("Cabin Leak", n_per_fault=50)
        nom_mean  = np.mean([ml.anomaly_score(r) for r in nominal_batch])
        fault_mean = np.mean([ml.anomaly_score(r) for r in fault_readings])
        assert fault_mean < nom_mean, \
            f"Fault score ({fault_mean:.3f}) not lower than nominal ({nom_mean:.3f})"


# ── SINGLE-THREADED INFERENCE (no FD leak) ────────────────────────────────────

class TestInferenceMode:
    def test_if_model_single_threaded(self, ml):
        if hasattr(ml.if_model, "n_jobs"):
            assert ml.if_model.n_jobs == 1

    def test_rf_model_single_threaded(self, ml):
        if hasattr(ml.rf_model, "n_jobs"):
            assert ml.rf_model.n_jobs == 1
