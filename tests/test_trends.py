"""
Tests for app/trend_detector.py — Mann-Kendall, Sen's slope, CUSUM,
rolling z-score, and the full analyze_parameter / analyze_location pipeline.
"""

import pytest
import numpy as np
from app import trend_detector, constants


# ── HELPERS ───────────────────────────────────────────────────────────────────

def monotone_increasing(n=50, start=1.0, step=0.1):
    return [start + i * step for i in range(n)]


def monotone_decreasing(n=50, start=5.0, step=0.1):
    return [start - i * step for i in range(n)]


def flat_series(n=50, val=2.0):
    return [val] * n


def random_series(n=50, seed=42):
    rng = np.random.default_rng(seed)
    return list(rng.standard_normal(n))


def step_up_series(n=50, step_at=25, baseline=1.0, after=3.0):
    return [baseline] * step_at + [after] * (n - step_at)


def step_down_series(n=50, step_at=25, baseline=3.0, after=0.5):
    return [baseline] * step_at + [after] * (n - step_at)


# ── MANN-KENDALL ──────────────────────────────────────────────────────────────

class TestMannKendall:
    def test_increasing_trend_detected(self):
        result = trend_detector.mann_kendall(monotone_increasing())
        assert result["trend"] == "increasing"
        assert result["significant"] is True

    def test_decreasing_trend_detected(self):
        result = trend_detector.mann_kendall(monotone_decreasing())
        assert result["trend"] == "decreasing"
        assert result["significant"] is True

    def test_tau_positive_for_increasing(self):
        result = trend_detector.mann_kendall(monotone_increasing())
        assert result["tau"] > 0

    def test_tau_negative_for_decreasing(self):
        result = trend_detector.mann_kendall(monotone_decreasing())
        assert result["tau"] < 0

    def test_tau_in_unit_interval(self):
        for series in [monotone_increasing(), monotone_decreasing(), random_series()]:
            result = trend_detector.mann_kendall(series)
            assert -1.0 <= result["tau"] <= 1.0

    def test_p_value_in_unit_interval(self):
        for series in [monotone_increasing(), monotone_decreasing(), random_series()]:
            result = trend_detector.mann_kendall(series)
            assert 0.0 <= result["p_value"] <= 1.0

    def test_strong_monotone_p_value_small(self):
        result = trend_detector.mann_kendall(monotone_increasing(n=100))
        assert result["p_value"] < 0.001

    def test_flat_series_no_trend(self):
        result = trend_detector.mann_kendall(flat_series())
        assert result["trend"] == "no trend"

    def test_short_series_returns_no_trend(self):
        result = trend_detector.mann_kendall([1.0, 2.0, 3.0])
        assert result["trend"] == "no trend"

    def test_result_has_required_keys(self):
        result = trend_detector.mann_kendall(monotone_increasing())
        for key in ("tau", "p_value", "trend", "significant"):
            assert key in result

    def test_returns_no_trend_for_random_white_noise(self):
        # Not strictly guaranteed, but very likely for large n
        rng = np.random.default_rng(0)
        series = list(rng.standard_normal(100))
        result = trend_detector.mann_kendall(series)
        # p_value should be non-tiny for white noise
        assert result["p_value"] > 0.001


# ── SEN'S SLOPE ───────────────────────────────────────────────────────────────

class TestSensSlope:
    def test_positive_for_increasing(self):
        slope = trend_detector.sens_slope(monotone_increasing())
        assert slope > 0

    def test_negative_for_decreasing(self):
        slope = trend_detector.sens_slope(monotone_decreasing())
        assert slope < 0

    def test_near_zero_for_flat(self):
        slope = trend_detector.sens_slope(flat_series())
        assert abs(slope) < 1e-10

    def test_magnitude_matches_step(self):
        step = 0.1
        slope = trend_detector.sens_slope(monotone_increasing(step=step))
        assert abs(slope - step) < 0.01

    def test_short_series_returns_zero(self):
        slope = trend_detector.sens_slope([5.0])
        assert slope == 0.0

    def test_returns_float(self):
        slope = trend_detector.sens_slope(monotone_increasing())
        assert isinstance(slope, float)


# ── CUSUM ──────────────────────────────────────────────────────────────────────

class TestCUSUM:
    def test_detects_step_up(self):
        result = trend_detector.cusum_change_point(step_up_series(n=60, step_at=30))
        assert result["detected"] is True
        assert result["direction"] == "up"

    def test_detects_step_down(self):
        result = trend_detector.cusum_change_point(step_down_series(n=60, step_at=30))
        assert result["detected"] is True
        assert result["direction"] == "down"

    def test_not_detected_for_flat(self):
        result = trend_detector.cusum_change_point(flat_series(n=50))
        assert result["detected"] is False

    def test_not_detected_for_white_noise(self):
        rng = np.random.default_rng(42)
        series = list(rng.standard_normal(100) * 0.01)
        result = trend_detector.cusum_change_point(series)
        # With tiny noise and high threshold, should not detect
        assert result["detected"] is False

    def test_short_series_not_detected(self):
        result = trend_detector.cusum_change_point([1.0, 2.0, 3.0])
        assert result["detected"] is False

    def test_change_index_after_step(self):
        result = trend_detector.cusum_change_point(step_up_series(n=80, step_at=40))
        if result["detected"]:
            assert result["change_index"] > 0

    def test_result_has_required_keys(self):
        result = trend_detector.cusum_change_point(step_up_series())
        for key in ("detected", "change_index", "direction"):
            assert key in result


# ── ROLLING Z-SCORE ───────────────────────────────────────────────────────────

class TestRollingZScore:
    def test_high_for_spike(self):
        series = flat_series(n=50, val=1.0)
        series[-1] = 100.0  # extreme spike
        z = trend_detector.rolling_zscore(series)
        assert z > 3.0, f"Expected high z-score, got {z}"

    def test_near_zero_for_baseline_value(self):
        series = [1.0] * 50
        z = trend_detector.rolling_zscore(series)
        assert abs(z) < 1e-6

    def test_zero_for_short_series(self):
        z = trend_detector.rolling_zscore([1.0, 2.0])
        assert z == 0.0

    def test_returns_float(self):
        z = trend_detector.rolling_zscore(monotone_increasing())
        assert isinstance(z, float)

    def test_negative_for_low_outlier(self):
        series = flat_series(n=50, val=5.0)
        series[-1] = -100.0
        z = trend_detector.rolling_zscore(series)
        assert z < -3.0, f"Expected negative z-score, got {z}"


# ── ANALYZE PARAMETER ─────────────────────────────────────────────────────────

class TestAnalyzeParameter:
    def _example_param(self):
        return "O2 partial pressure"

    def test_returns_dict(self):
        result = trend_detector.analyze_parameter(self._example_param(), monotone_increasing())
        assert isinstance(result, dict)

    def test_has_required_keys(self):
        result = trend_detector.analyze_parameter(self._example_param(), monotone_increasing(n=30))
        for key in ("param", "unit", "n", "current_value", "nominal_range",
                    "mann_kendall", "sens_slope_per_reading", "cusum", "z_score", "severity"):
            assert key in result, f"Missing key {key!r}"

    def test_insufficient_data_returns_early(self):
        result = trend_detector.analyze_parameter(self._example_param(), [1.0, 2.0])
        assert result["status"] == "insufficient_data"

    def test_severity_valid_value(self):
        valid = {"critical", "warning", "advisory", "nominal", "insufficient_data"}
        result = trend_detector.analyze_parameter(self._example_param(), monotone_increasing(n=30))
        assert result["severity"] in valid

    def test_out_of_range_current_value_critical(self):
        # O2 partial pressure nominal: (19.5, 23.1). Value 5.0 is way below.
        series = [5.0] * 30
        result = trend_detector.analyze_parameter("O2 partial pressure", series)
        assert result["severity"] == "critical"

    def test_nominal_series_nominal_severity(self):
        lo, hi = constants.PARAMETER_NOMINAL_RANGES["O2 partial pressure"]
        mid = (lo + hi) / 2
        series = [mid + np.random.default_rng(i).standard_normal() * 0.01
                  for i in range(40)]
        result = trend_detector.analyze_parameter("O2 partial pressure", series)
        assert result["severity"] in {"nominal", "advisory"}

    def test_current_value_matches_last_entry(self):
        series = monotone_increasing(n=20)
        result = trend_detector.analyze_parameter(self._example_param(), series)
        assert abs(result["current_value"] - series[-1]) < 1e-4

    def test_n_matches_series_length(self):
        series = monotone_increasing(n=25)
        result = trend_detector.analyze_parameter(self._example_param(), series)
        assert result["n"] == 25

    def test_unit_string(self):
        result = trend_detector.analyze_parameter(self._example_param(), monotone_increasing(n=20))
        assert isinstance(result["unit"], str)

    def test_recommendation_string(self):
        result = trend_detector.analyze_parameter(self._example_param(), monotone_increasing(n=30))
        assert isinstance(result.get("recommendation"), str)


# ── ANALYZE LOCATION ──────────────────────────────────────────────────────────

class TestAnalyzeLocation:
    def test_returns_list(self):
        history = {p: monotone_increasing() for p in list(constants.PARAMETER_NOMINAL_RANGES.keys())[:5]}
        results = trend_detector.analyze_location("US Lab", history)
        assert isinstance(results, list)

    def test_one_result_per_param(self):
        params = list(constants.PARAMETER_NOMINAL_RANGES.keys())[:5]
        history = {p: monotone_increasing() for p in params}
        results = trend_detector.analyze_location("US Lab", history)
        assert len(results) == 5

    def test_sorted_by_severity(self):
        severity_order = {"critical": 0, "warning": 1, "advisory": 2, "nominal": 3, "insufficient_data": 4}
        params = list(constants.PARAMETER_NOMINAL_RANGES.keys())
        history = {}
        # Mix of out-of-range (critical) and nominal
        lo, hi = constants.PARAMETER_NOMINAL_RANGES[params[0]]
        history[params[0]] = [lo - 5.0] * 30  # critical
        for p in params[1:4]:
            lo2, hi2 = constants.PARAMETER_NOMINAL_RANGES[p]
            history[p] = [(lo2 + hi2) / 2] * 30  # nominal

        results = trend_detector.analyze_location("US Lab", history)
        severities = [r.get("severity", "nominal") for r in results]
        for i in range(len(severities) - 1):
            assert severity_order.get(severities[i], 99) <= \
                   severity_order.get(severities[i + 1], 99), \
                   f"Not sorted: {severities[i]} > {severities[i+1]}"

    def test_empty_history_returns_empty(self):
        results = trend_detector.analyze_location("US Lab", {})
        assert results == []
