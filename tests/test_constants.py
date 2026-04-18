"""
Tests for app/constants.py — validates every constant table for internal
consistency and physical plausibility before the server accepts any data.
"""

import pytest
from app import constants


# ── LOCATIONS ────────────────────────────────────────────────────────────────

class TestLocations:
    def test_locations_not_empty(self):
        assert len(constants.LOCATIONS) > 0

    def test_locations_are_strings(self):
        for loc in constants.LOCATIONS:
            assert isinstance(loc, str) and loc.strip()

    def test_location_positions_cover_all_locations(self):
        for loc in constants.LOCATIONS:
            assert loc in constants.LOCATION_POSITIONS, f"No position for {loc!r}"

    def test_location_positions_are_2d_tuples(self):
        for loc, pos in constants.LOCATION_POSITIONS.items():
            assert len(pos) == 2, f"{loc!r} position is not 2-tuple"
            assert all(isinstance(v, (int, float)) for v in pos)


# ── PARAMETERS ───────────────────────────────────────────────────────────────

class TestParameters:
    def _all_params(self):
        return list(constants.PARAMETER_NOMINAL_RANGES.keys())

    def test_nominal_ranges_not_empty(self):
        assert len(constants.PARAMETER_NOMINAL_RANGES) > 0

    def test_nominal_range_lo_lt_hi(self):
        for param, (lo, hi) in constants.PARAMETER_NOMINAL_RANGES.items():
            assert lo < hi, f"{param}: lo={lo} >= hi={hi}"

    def test_physical_limits_cover_all_params(self):
        for param in constants.PARAMETER_NOMINAL_RANGES:
            assert param in constants.PHYSICAL_LIMITS, f"No physical limit for {param!r}"

    def test_physical_limits_lo_lt_hi(self):
        for param, (lo, hi) in constants.PHYSICAL_LIMITS.items():
            assert lo < hi, f"{param}: physical lo={lo} >= hi={hi}"

    def test_nominal_range_within_physical_limits(self):
        for param, (nom_lo, nom_hi) in constants.PARAMETER_NOMINAL_RANGES.items():
            if param in constants.PHYSICAL_LIMITS:
                phys_lo, phys_hi = constants.PHYSICAL_LIMITS[param]
                assert nom_lo >= phys_lo, f"{param}: nominal lo below physical lo"
                assert nom_hi <= phys_hi, f"{param}: nominal hi above physical hi"

    def test_units_cover_all_params(self):
        for param in constants.PARAMETER_NOMINAL_RANGES:
            assert param in constants.PARAMETER_UNITS, f"No unit for {param!r}"

    def test_units_are_strings(self):
        for param, unit in constants.PARAMETER_UNITS.items():
            assert isinstance(unit, str)

    def test_noise_sigma_cover_all_params(self):
        for param in constants.PARAMETER_NOMINAL_RANGES:
            assert param in constants.SENSOR_NOISE_SIGMA, f"No noise sigma for {param!r}"

    def test_noise_sigma_positive(self):
        for param, sigma in constants.SENSOR_NOISE_SIGMA.items():
            assert sigma > 0, f"{param}: sigma={sigma} not positive"
            assert sigma < 1.0, f"{param}: sigma={sigma} unreasonably large"

    def test_subsystem_params_exist_in_nominal_ranges(self):
        for subsys, params in constants.SUBSYSTEM_PARAMETERS.items():
            for p in params:
                assert p in constants.PARAMETER_NOMINAL_RANGES, \
                    f"Subsystem {subsys!r} param {p!r} not in PARAMETER_NOMINAL_RANGES"


# ── FAULTS ───────────────────────────────────────────────────────────────────

class TestFaults:
    def test_faults_not_empty(self):
        assert len(constants.FAULT_IMPACT_SEVERITY) > 0

    def test_each_fault_has_impacts(self):
        for fault, data in constants.FAULT_IMPACT_SEVERITY.items():
            assert "impacts" in data, f"{fault!r} missing 'impacts'"
            assert len(data["impacts"]) > 0, f"{fault!r} has empty impacts"

    def test_fault_impact_params_exist(self):
        for fault, data in constants.FAULT_IMPACT_SEVERITY.items():
            for param in data["impacts"]:
                assert param in constants.PARAMETER_NOMINAL_RANGES, \
                    f"Fault {fault!r} impacts unknown param {param!r}"

    def test_fault_impact_coefficients_nonzero(self):
        for fault, data in constants.FAULT_IMPACT_SEVERITY.items():
            for param, coeff in data["impacts"].items():
                assert coeff != 0, f"{fault!r} → {param!r}: coeff is zero"

    def test_precursor_hours_covers_all_faults(self):
        for fault in constants.FAULT_IMPACT_SEVERITY:
            assert fault in constants.FAULT_PRECURSOR_HOURS, \
                f"No precursor_hours for {fault!r}"

    def test_precursor_hours_positive(self):
        for fault, hours in constants.FAULT_PRECURSOR_HOURS.items():
            assert hours > 0, f"{fault!r}: precursor_hours={hours} not positive"

    def test_mtbf_covers_all_faults(self):
        for fault in constants.FAULT_IMPACT_SEVERITY:
            assert fault in constants.SENSOR_MTBF_HOURS, f"No MTBF for {fault!r}"

    def test_mtbf_positive(self):
        for fault, hours in constants.SENSOR_MTBF_HOURS.items():
            assert hours > 0, f"{fault!r}: MTBF={hours} not positive"

    def test_maintenance_recommendations_covers_all_faults(self):
        for fault in constants.FAULT_IMPACT_SEVERITY:
            assert fault in constants.MAINTENANCE_RECOMMENDATIONS, \
                f"No maintenance recommendation for {fault!r}"

    def test_maintenance_recommendations_have_required_keys(self):
        required = {"subsystem", "maintenance_type", "primary_action"}
        for fault, rec in constants.MAINTENANCE_RECOMMENDATIONS.items():
            for key in required:
                assert key in rec, f"Fault {fault!r} rec missing {key!r}"


# ── ACTIONS ──────────────────────────────────────────────────────────────────

class TestActions:
    def test_actions_not_empty(self):
        assert len(constants.ACTIONS_TO_TAKE) > 0

    def test_first_action_is_no_action(self):
        assert constants.ACTIONS_TO_TAKE[0] == "No Action Needed"

    def test_actions_to_fault_maps_correctly(self):
        for action, fault in constants.ACTIONS_TO_FAULT.items():
            assert fault in constants.FAULT_IMPACT_SEVERITY, \
                f"ACTIONS_TO_FAULT maps to unknown fault {fault!r}"
            assert action in constants.ACTIONS_TO_TAKE, \
                f"ACTIONS_TO_FAULT references unknown action {action!r}"

    def test_no_duplicate_actions(self):
        assert len(constants.ACTIONS_TO_TAKE) == len(set(constants.ACTIONS_TO_TAKE))


# ── CALIBRATION DRIFT ────────────────────────────────────────────────────────

class TestCalibration:
    def test_drift_params_exist(self):
        for param in constants.CALIBRATION_DRIFT_PER_WEEK:
            assert param in constants.PARAMETER_NOMINAL_RANGES, \
                f"Calibration drift for unknown param {param!r}"

    def test_drift_rates_positive(self):
        for param, rate in constants.CALIBRATION_DRIFT_PER_WEEK.items():
            assert rate > 0, f"{param}: drift rate={rate} not positive"
            assert rate < 1.0, f"{param}: drift rate={rate} unreasonably large"


# ── CORRELATION MATRIX ────────────────────────────────────────────────────────

class TestCorrelationMatrix:
    def test_correlation_params_exist(self):
        for (p1, p2) in constants.PARAMETER_CORRELATION_MATRIX:
            assert p1 in constants.PARAMETER_NOMINAL_RANGES, f"Unknown param {p1!r}"
            assert p2 in constants.PARAMETER_NOMINAL_RANGES, f"Unknown param {p2!r}"

    def test_correlation_values_in_range(self):
        for (p1, p2), r in constants.PARAMETER_CORRELATION_MATRIX.items():
            assert -1.0 <= r <= 1.0, f"({p1},{p2}): r={r} outside [-1,1]"
            assert r != 0.0, f"({p1},{p2}): zero correlation is useless"


# ── DATA GENERATION CONSTANTS ────────────────────────────────────────────────

class TestDataGenerationConstants:
    def test_generation_interval_positive(self):
        assert constants.DATA_GENERATION_INTERVAL > 0

    def test_database_path_is_string(self):
        assert isinstance(constants.DATABASE_PATH, str)

    def test_model_paths_are_strings(self):
        assert isinstance(constants.IF_MODEL_PATH, str)
        assert isinstance(constants.RF_MODEL_PATH, str)
