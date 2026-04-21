"""
Tests for app/data_generator.py — nominal sampling, fault drift direction,
physical clamping, correlated noise, and training-batch generators.
"""

import pytest
import numpy as np
from app import constants
from app.data_generator import SensorDataGenerator, _build_cholesky


@pytest.fixture
def gen():
    return SensorDataGenerator(seed=42, step_seconds=1.0)


NOMINAL_PARAMS = list(constants.PARAMETER_NOMINAL_RANGES.keys())
ALL_FAULTS     = list(constants.FAULT_IMPACT_SEVERITY.keys())


# ── CHOLESKY MATRIX ───────────────────────────────────────────────────────────

class TestCholesky:
    def test_cholesky_returns_matrix(self):
        L = _build_cholesky(NOMINAL_PARAMS)
        assert L.shape[0] == len(NOMINAL_PARAMS)
        assert L.shape[1] == len(NOMINAL_PARAMS)

    def test_cholesky_lower_triangular(self):
        L = _build_cholesky(NOMINAL_PARAMS)
        for i in range(len(L)):
            for j in range(i + 1, len(L)):
                assert L[i, j] == 0.0, f"L[{i},{j}] should be 0 (lower triangular)"

    def test_correlation_matrix_is_positive_definite(self):
        L = _build_cholesky(NOMINAL_PARAMS)
        C = L @ L.T
        eigenvalues = np.linalg.eigvalsh(C)
        assert all(e > 0 for e in eigenvalues), "Correlation matrix is not PD"


# ── NOMINAL SAMPLING ─────────────────────────────────────────────────────────

class TestNominalSampling:
    def test_sample_returns_all_params(self, gen):
        reading = gen.sample(constants.LOCATIONS[0])
        assert set(reading.keys()) == set(NOMINAL_PARAMS)

    def test_sample_all_float(self, gen):
        reading = gen.sample(constants.LOCATIONS[0])
        for param, val in reading.items():
            assert isinstance(val, float), f"{param} is not float"

    def test_sample_within_physical_limits(self, gen):
        for _ in range(20):
            reading = gen.sample(constants.LOCATIONS[0])
            for param, val in reading.items():
                lo, hi = constants.PHYSICAL_LIMITS[param]
                assert lo <= val <= hi, \
                    f"{param}={val} outside physical [{lo},{hi}]"

    def test_sample_near_nominal_range(self, gen):
        # Nominal sample should be close to nominal range center
        # (allowing for noise — check within 3x the nominal span)
        reading = gen.sample(constants.LOCATIONS[0])
        for param, val in reading.items():
            lo, hi = constants.PARAMETER_NOMINAL_RANGES[param]
            span = hi - lo
            assert lo - 3 * span <= val <= hi + 3 * span, \
                f"{param}={val} too far from nominal [{lo},{hi}]"

    def test_mission_elapsed_hours_increases(self, gen):
        t0 = gen.mission_elapsed_hours
        gen.sample(constants.LOCATIONS[0])
        assert gen.mission_elapsed_hours > t0

    def test_sample_different_each_call(self, gen):
        loc = constants.LOCATIONS[0]
        r1 = gen.sample(loc)
        r2 = gen.sample(loc)
        # At least some params should differ (noise-driven)
        diffs = sum(1 for p in NOMINAL_PARAMS if r1[p] != r2[p])
        assert diffs > 0

    def test_all_locations_produce_readings(self, gen):
        for loc in constants.LOCATIONS:
            reading = gen.sample(loc)
            assert len(reading) == len(NOMINAL_PARAMS)


# ── FAULT DRIFT ───────────────────────────────────────────────────────────────

class TestFaultDrift:
    def _measure_drift(self, fault, affected_param, n=200):
        """Run N fault ticks and return mean value of affected param."""
        g = SensorDataGenerator(seed=1, step_seconds=3600.0)
        g.sample(constants.LOCATIONS[0], active_fault=None)  # warm up
        vals = [g.sample(constants.LOCATIONS[0], active_fault=fault)[affected_param]
                for _ in range(n)]
        return np.mean(vals)

    def _nominal_mean(self, param, n=50):
        g = SensorDataGenerator(seed=2, step_seconds=1.0)
        vals = [g.sample(constants.LOCATIONS[0])[param] for _ in range(n)]
        return np.mean(vals)

    def test_fault_drifts_in_expected_direction(self):
        # Cabin Leak should drop cabin pressure
        fault = "Cabin Leak"
        param = "Cabin pressure"
        coeff = constants.FAULT_IMPACT_SEVERITY[fault]["impacts"][param]
        assert coeff < 0, "Expected negative coefficient for cabin pressure in Cabin Leak"

        fault_mean = self._measure_drift(fault, param)
        nominal_mean = self._nominal_mean(param)
        # After 200 fault ticks at 3600s each, pressure should be significantly lower
        assert fault_mean < nominal_mean, \
            f"Cabin pressure didn't drop under Cabin Leak (fault={fault_mean:.3f} vs nominal={nominal_mean:.3f})"

    def test_o2_generator_failure_drops_o2_output(self):
        fault = "O2 Generator Failure"
        param = "O2 output rate (generator)"
        coeff = constants.FAULT_IMPACT_SEVERITY[fault]["impacts"][param]
        assert coeff < 0

    def test_co2_scrubber_failure_raises_co2(self):
        fault = "CO2 Scrubber Failure"
        param = "CO2 partial pressure"
        coeff = constants.FAULT_IMPACT_SEVERITY[fault]["impacts"][param]
        assert coeff > 0

    def test_chx_failure_raises_humidity(self):
        fault = "CHX Failure"
        param = "Humidity"
        coeff = constants.FAULT_IMPACT_SEVERITY[fault]["impacts"][param]
        assert coeff > 0

    def test_reset_drift_clears_per_location(self):
        g = SensorDataGenerator(seed=3, step_seconds=3600.0)
        loc = constants.LOCATIONS[0]
        fault = "Cabin Leak"
        for _ in range(10):
            g.sample(loc, active_fault=fault)
        g.reset_drift(loc)
        # After reset, drift state should be zeroed
        assert all(v == 0.0 for v in g._drift[loc].values())

    def test_reset_drift_none_resets_all(self):
        g = SensorDataGenerator(seed=4, step_seconds=3600.0)
        fault = "O2 Leak"
        for loc in constants.LOCATIONS:
            g.sample(loc, active_fault=fault)
        g.reset_drift()
        for loc in constants.LOCATIONS:
            assert all(v == 0.0 for v in g._drift[loc].values())

    def test_fault_transition_resets_drift(self):
        g = SensorDataGenerator(seed=5, step_seconds=3600.0)
        loc = constants.LOCATIONS[0]
        for _ in range(5):
            g.sample(loc, active_fault="Cabin Leak")
        # Switch fault — drift should reset
        g.sample(loc, active_fault="O2 Leak")
        # Previous Cabin Leak drift should be cleared
        assert g._drift[loc]["Cabin pressure"] == 0.0 or \
               abs(g._drift[loc].get("Cabin pressure", 0.0)) < 0.1


# ── TRAINING BATCH GENERATORS ────────────────────────────────────────────────

class TestTrainingBatches:
    def test_generate_nominal_batch_count(self):
        g = SensorDataGenerator(seed=10)
        batch = g.generate_nominal_batch(n=100)
        assert len(batch) == 100

    def test_generate_nominal_batch_all_have_params(self):
        g = SensorDataGenerator(seed=10)
        batch = g.generate_nominal_batch(n=10)
        for row in batch:
            assert set(row.keys()) == set(NOMINAL_PARAMS)

    def test_generate_fault_batch_count(self):
        g = SensorDataGenerator(seed=11)
        readings, labels = g.generate_fault_batch("Cabin Leak", n_per_fault=30)
        assert len(readings) == 30
        assert len(labels) == 30

    def test_generate_fault_batch_labels(self):
        g = SensorDataGenerator(seed=11)
        _, labels = g.generate_fault_batch("Cabin Leak", n_per_fault=20)
        assert all(l == "Cabin Leak" for l in labels)

    def test_generate_sequence_structure(self):
        g = SensorDataGenerator(seed=12)
        seq = g.generate_sequence(n=50, fault="Cabin Leak", fault_start=25)
        assert len(seq) == 50
        for record in seq:
            assert "data" in record
            assert "anomaly" in record
            assert "rul_hours" in record

    def test_generate_sequence_anomaly_starts_at_fault(self):
        g = SensorDataGenerator(seed=13, step_seconds=1.0)
        fault_start = 30
        seq = g.generate_sequence(n=60, fault="Cabin Leak", fault_start=fault_start)
        # Anomaly flag should be 0 before fault starts
        assert all(r["anomaly"] == 0 for r in seq[:fault_start])

    def test_generate_sequence_no_fault_all_nominal(self):
        g = SensorDataGenerator(seed=14)
        seq = g.generate_sequence(n=30)
        assert all(r["anomaly"] == 0 for r in seq)
        assert all(r["rul_hours"] is None for r in seq)

    def test_generate_nominal_does_not_pollute_runtime_state(self):
        g = SensorDataGenerator(seed=15)
        t_before = g._mission_seconds
        g.generate_nominal_batch(n=100)
        assert g._mission_seconds == t_before  # state restored

    def test_generate_fault_batch_does_not_pollute_runtime_state(self):
        g = SensorDataGenerator(seed=16)
        drift_before = {loc: dict(d) for loc, d in g._drift.items()}
        g.generate_fault_batch("Cabin Leak", n_per_fault=50)
        for loc in constants.LOCATIONS:
            assert g._drift[loc] == drift_before[loc]
