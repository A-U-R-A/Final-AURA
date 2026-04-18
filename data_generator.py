"""
Synthetic ECLSS sensor data generator.

Tier 1 — Nominal operation with:
  • Cholesky-decomposed correlated noise
  • Circadian metabolic rhythms (O2/CO2 vary with crew activity)
  • Long-term nominal aging drift
  • Crew activity events (exercise, meal, EVA prep)

Tier 2 — Fault injection:
  • Physics-based fault drift accumulation (ported from Hunch-AURA)
  • Per-location drift isolation
  • Automatic drift reset on fault clearance

Ported and upgraded from Hunch-AURA/src/dataHandling/rfDataGeneration.py
"""

import numpy as np
import constants

# ── Circadian profiles (24 h, sampled at 0,2,4,...,24 h) ──────────────────────
_CIRC_H    = np.array([0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24], dtype=float)
_CIRC_CO2  = np.array([1.0, 0.7, 0.6, 0.8, 1.4, 1.2, 1.1, 1.0, 1.2, 1.5, 1.3, 1.1, 1.0])
_CIRC_O2   = np.array([1.0, 0.8, 0.7, 0.9, 1.3, 1.1, 1.0, 1.0, 1.1, 1.4, 1.2, 1.0, 1.0])


def _build_cholesky(params: list) -> np.ndarray:
    """Build lower-triangular Cholesky factor L from PARAMETER_CORRELATION_MATRIX.

    Usage: z @ L.T where z ~ N(0,I) produces correlated noise with the specified
    Pearson correlations. Called once at generator init; result stored in self._L.

    If the assembled correlation matrix is not positive-definite (can happen with
    a sparse or inconsistent set of pairwise r values), a small diagonal jitter
    is added to make it PD before decomposition."""
    n = len(params)
    C = np.eye(n)
    idx = {p: i for i, p in enumerate(params)}
    for (p1, p2), r in constants.PARAMETER_CORRELATION_MATRIX.items():
        if p1 in idx and p2 in idx:
            i, j = idx[p1], idx[p2]
            C[i, j] = r
            C[j, i] = r
    # If smallest eigenvalue is near zero, the matrix is not positive-definite.
    # Shifting the diagonal by |min_eig| + ε guarantees PD and keeps correlations close.
    min_eig = np.linalg.eigvalsh(C).min()
    if min_eig < 1e-6:
        C += np.eye(n) * (abs(min_eig) + 1e-4)
    return np.linalg.cholesky(C)


class SensorDataGenerator:
    """
    Physics-informed synthetic ECLSS sensor data generator.

    Usage (runtime / background loop):
        gen = SensorDataGenerator()
        reading = gen.sample("JLP & JPM", active_fault="Cabin Leak")

    Usage (training — generate a labeled sequence):
        gen = SensorDataGenerator(seed=42)
        seq = gen.generate_sequence(n=200, fault="Cabin Leak", fault_start=100)
        # returns list of {data, anomaly, rul_hours}
    """

    def __init__(
        self,
        seed: int = None,
        drift_range: tuple = (1.0, 1.5),
        crew_size: int = 4,
        step_seconds: float = 1.0,   # wall seconds per sample() call
    ):
        self.rng = np.random.default_rng(seed)
        self.drift_range = drift_range
        self.crew_size = crew_size
        self.step_seconds = step_seconds

        self._params = list(constants.PARAMETER_NOMINAL_RANGES.keys())
        self._n_params = len(self._params)
        self._L = _build_cholesky(self._params)

        # Runtime state — per-location
        self._drift: dict[str, dict] = {}
        self._prev_faults: dict[str, str | None] = {}

        # Mission clock (simulated time, shared across all locations)
        # Advances by step_seconds per sample() call
        self._mission_seconds: float = 0.0

        self._init_locations()

    # ── Initialisation ────────────────────────────────────────────────────────

    def _init_locations(self):
        zero = {p: 0.0 for p in self._params}
        for loc in constants.LOCATIONS:
            self._drift[loc] = dict(zero)
            self._prev_faults[loc] = None

    # ── Public API — runtime ──────────────────────────────────────────────────

    def sample(self, location: str, active_fault: str = None) -> dict:
        """Generate one sensor reading for a location."""
        self._handle_fault_transition(location, active_fault)
        self._mission_seconds += self.step_seconds

        hour_of_day  = (self._mission_seconds / 3600.0) % 24.0
        day_of_mission = self._mission_seconds / 86400.0

        row = self._baseline(hour_of_day, day_of_mission)

        # Rare crew activity event (~0.5 % chance per tick)
        if self.rng.random() < 0.005:
            row = self._crew_event(row, hour_of_day)

        # Apply fault drift — time-scaled so coefficients are "nominal spans per hour"
        # drift_per_tick = coeff * nominal_span * rate * (step_seconds / 3600)
        # This ensures realistic fault progression regardless of tick rate.
        local_drift = self._drift[location]
        if active_fault is not None:
            impacts = constants.FAULT_IMPACT_SEVERITY[active_fault]["impacts"]
            time_scale = self.step_seconds / 3600.0
            for p, base_coeff in impacts.items():
                if p not in local_drift:
                    continue
                span = constants.PARAMETER_NOMINAL_RANGES[p][1] - constants.PARAMETER_NOMINAL_RANGES[p][0]
                rate = self.rng.uniform(*self.drift_range)
                local_drift[p] += base_coeff * span * rate * time_scale

            for p in row:
                row[p] += local_drift[p]
                lo, hi = constants.PHYSICAL_LIMITS[p]
                row[p] = float(np.clip(row[p], lo, hi))
        else:
            for p in row:
                row[p] += local_drift.get(p, 0.0)
                lo, hi = constants.PARAMETER_NOMINAL_RANGES[p]
                row[p] = float(np.clip(row[p], lo, hi))

        return row

    @property
    def mission_elapsed_hours(self) -> float:
        """Simulated mission time elapsed in hours."""
        return self._mission_seconds / 3600.0

    def reset_drift(self, location: str = None):
        """Reset fault drift. If location is None, resets all locations."""
        targets = [location] if location else list(constants.LOCATIONS)
        zero = {p: 0.0 for p in self._params}
        for loc in targets:
            self._drift[loc] = dict(zero)
            self._prev_faults[loc] = None

# ── Public API — training ─────────────────────────────────────────────────

    def generate_sequence(
        self,
        n: int = 200,
        fault: str = None,
        fault_start: int = None,
        location: str = "JLP & JPM",
    ) -> list[dict]:
        """
        Generate a labeled sequence of n sensor readings.

        Returns a list of dicts:
            {data: {param: value}, anomaly: 0|1, rul_hours: float|None}

        If fault is provided:
          - fault_start defaults to n // 2
          - anomaly becomes 1 once the fault is 50 % progressed
          - rul_hours counts down from FAULT_PRECURSOR_HOURS to 0
        """
        if fault and fault_start is None:
            fault_start = n // 2

        precursor_hours = (
            constants.FAULT_PRECURSOR_HOURS.get(fault, 48.0) if fault else None
        )
        # How many steps represent precursor_hours? Each step = step_seconds
        precursor_steps = int(precursor_hours * 3600 / self.step_seconds) if precursor_hours else 0

        # Temporarily clone generator state so this doesn't pollute runtime
        saved_drift = {loc: dict(d) for loc, d in self._drift.items()}
        saved_prev  = dict(self._prev_faults)
        saved_ms    = self._mission_seconds

        self.reset_drift(location)
        records = []

        for i in range(n):
            active = fault if (fault and i >= fault_start) else None
            reading = self.sample(location, active_fault=active)

            anomaly = 0
            rul = None
            if fault and i >= fault_start:
                elapsed = i - fault_start
                rul = max(0.0, (precursor_steps - elapsed) / 3600.0 * self.step_seconds)
                if elapsed >= precursor_steps * 0.5:
                    anomaly = 1

            records.append({"data": reading, "anomaly": anomaly, "rul_hours": rul})

        # Restore state
        self._drift = saved_drift
        self._prev_faults = saved_prev
        self._mission_seconds = saved_ms

        return records

    def generate_nominal_batch(self, n: int = 5000, location: str = "JLP & JPM") -> list:
        """
        Generate n nominal (healthy) readings.

        Step size is stretched so the batch always spans at least one full
        24-hour circadian cycle — this ensures the IF/RF models learn the
        complete daily distribution and don't false-positive at certain hours.
        """
        saved_drift = {loc: dict(d) for loc, d in self._drift.items()}
        saved_prev  = dict(self._prev_faults)
        saved_ms    = self._mission_seconds
        saved_step  = self.step_seconds

        self.reset_drift(location)
        self._mission_seconds = 0.0
        # Each sample advances by 86400/n seconds so n samples = exactly 1 day
        self.step_seconds = max(1.0, 86400.0 / n)

        batch = [self.sample(location, active_fault=None) for _ in range(n)]

        self._drift         = saved_drift
        self._prev_faults   = saved_prev
        self._mission_seconds = saved_ms
        self.step_seconds   = saved_step
        return batch

    def generate_fault_batch(
        self, fault: str, n_per_fault: int = 500, location: str = "JLP & JPM"
    ) -> tuple[list, list]:
        """
        Generate n_per_fault labeled fault readings.
        Returns (X_list, labels) where labels are fault name strings.
        """
        self.reset_drift(location)
        self._prev_faults[location] = None

        # Warm up with some nominal samples first so drift starts clean
        for _ in range(20):
            self.sample(location, active_fault=None)

        readings, labels = [], []
        for _ in range(n_per_fault):
            row = self.sample(location, active_fault=fault)
            readings.append(row)
            labels.append(fault)

        self.reset_drift(location)
        return readings, labels

    # ── Internals ─────────────────────────────────────────────────────────────

    def _baseline(self, hour_of_day: float, day_of_mission: float) -> dict:
        """Generate one set of nominal sensor readings with three noise layers:
          1. Correlated process noise  — shared physicial fluctuations between sensors (e.g.
             O2↓ when CO2↑). 1% of nominal span per draw via the pre-built Cholesky factor.
          2. Independent sensor noise  — each sensor's own measurement uncertainty (from
             SENSOR_NOISE_SIGMA, fraction of span).
          3. Long-term drift           — cumulative aging bias and calibration offset that
             grows slowly over the mission, making the dataset non-stationary over time."""
        # Correlated process noise: one shared draw → multiply by L.T for cross-param coupling
        z = self.rng.standard_normal(self._n_params)
        corr_z = z @ self._L.T

        # Independent sensor noise: drawn separately so each instrument has its own floor
        sensor_z = self.rng.standard_normal(self._n_params)

        row = {}
        for j, p in enumerate(self._params):
            lo, hi = constants.PARAMETER_NOMINAL_RANGES[p]
            center = (lo + hi) / 2.0
            span   = hi - lo

            sigma = constants.SENSOR_NOISE_SIGMA.get(p, 0.02)
            val = center + corr_z[j] * 0.01 * span + sensor_z[j] * sigma * span

            # Circadian modulation: crew metabolic rhythms shift CO2/O2/temp throughout the day
            val *= self._circadian_factor(p, hour_of_day)

            # Nominal aging: gradual component wear over mission duration
            val += constants.NOMINAL_AGING_PER_DAY.get(p, 0.0) * day_of_mission

            # Calibration drift: sensor bias accumulates over weeks (MCA, galvanic O2, TOCA)
            # Randomised ±20% each tick so drift is noisy rather than perfectly linear
            cal_drift_rate = constants.CALIBRATION_DRIFT_PER_WEEK.get(p, 0.0)
            weeks_elapsed  = day_of_mission / 7.0
            val += cal_drift_rate * span * weeks_elapsed * self.rng.uniform(0.8, 1.2)

            row[p] = float(np.clip(val, lo, hi))

        return row

    def _circadian_factor(self, param: str, hour: float) -> float:
        """
        Multiplicative circadian scaling around 1.0 (most params = 1.0).

        All modulations are kept small (±5-10% of center) so values stay
        well within nominal range regardless of time of day.
        The raw circadian profile arrays vary 0.6-1.5; we compress that
        deviation to ±10% max to avoid driving parameters out of range.
        """
        if param == "CO2 partial pressure":
            base = float(np.interp(hour, _CIRC_H, _CIRC_CO2))
            # ±6% max modulation of center; crew-size-weighted
            return 1.0 + (base - 1.0) * 0.06 * (self.crew_size / 4.0)
        if param in ("O2 partial pressure", "O2"):
            factor = float(np.interp(hour, _CIRC_H, _CIRC_O2))
            # ±2% max modulation — keeps values well inside nominal range all day
            return 1.0 - (factor - 1.0) * 0.02
        if param == "Temperature":
            factor = float(np.interp(hour, _CIRC_H, _CIRC_O2))
            return 1.0 + 0.01 * (factor - 1.0)
        return 1.0

    def _crew_event(self, row: dict, hour: float) -> dict:
        """Apply a random crew activity spike to simulate metabolic or operational events.
        All deltas are capped at the nominal range ceiling to avoid false anomaly triggers."""
        event = self.rng.choice(["exercise", "meal", "eva_prep"])
        nom = constants.PARAMETER_NOMINAL_RANGES

        if event == "exercise":
            # Exercise: crew CO2 output spikes, O2 consumption up, temp and humidity rise
            row["CO2 partial pressure"] = min(
                row["CO2 partial pressure"] * 1.35,
                nom["CO2 partial pressure"][1] * 0.98,
            )
            row["O2 partial pressure"] = max(
                row["O2 partial pressure"] * 0.97,
                nom["O2 partial pressure"][0] * 1.01,
            )
            row["Temperature"] = min(row["Temperature"] + 0.8, nom["Temperature"][1])
            row["Humidity"]    = min(row["Humidity"] + 0.06, nom["Humidity"][1])

        elif event == "meal":
            # Meal: slight CO2 increase from activity, trace CO/CH4 from food prep
            row["CO2 partial pressure"] = min(
                row["CO2 partial pressure"] * 1.15,
                nom["CO2 partial pressure"][1] * 0.95,
            )
            row["CO"]  = min(row["CO"]  + 0.3, nom["CO"][1])   # ~0.3 ppm spike (was 3.0 at old scale)
            row["CH4"] = min(row["CH4"] + 1.2, nom["CH4"][1])

        elif event == "eva_prep":
            row["O2 partial pressure"] = min(
                row["O2 partial pressure"] + 0.4, nom["O2 partial pressure"][1]
            )
            row["N2"] = max(row["N2"] - 0.003, nom["N2"][0])
            row["Cabin pressure"] = min(row["Cabin pressure"] + 0.12, nom["Cabin pressure"][1])

        return row

    def _handle_fault_transition(self, location: str, active_fault: str | None):
        """Reset accumulated drift whenever the fault changes or clears.
        Without this reset, leftover drift from a previous fault would contaminate
        readings for a new fault, producing unrealistic sensor signatures."""
        prev = self._prev_faults.get(location)
        if prev != active_fault:
            # Fault cleared (→ None) or swapped to a different fault — wipe the drift slate
            if active_fault is None or (prev is not None and active_fault != prev):
                self._drift[location] = {p: 0.0 for p in self._params}
        self._prev_faults[location] = active_fault
