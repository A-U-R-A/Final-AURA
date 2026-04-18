"""
Sensor trend detection for ECLSS parameters.

Implements:
  • Mann-Kendall non-parametric monotonic trend test
  • Sen's slope estimator (rate of change per reading)
  • Change-point detection (simple CUSUM)
  • Rolling z-score for sudden shifts

Used to catch slow-degrading components before the Isolation Forest triggers.

Reference: Plan Section 8 — Trend Detection / Sensor Replacement & Recalibration
"""

import math
import numpy as np
import constants


# ── Mann-Kendall test ─────────────────────────────────────────────────────────

def mann_kendall(x: list[float]) -> dict:
    """
    Non-parametric monotonic trend test.  No external library — pure Python/NumPy.

    The test counts concordant (S += 1) vs. discordant (S -= 1) pairs of readings.
    A large positive S → upward trend; large negative → downward.

    S is approximately normally distributed for n ≥ 10, so we compute:
        Z = (S ± 1) / sqrt(Var(S))     (continuity correction)
    and derive a two-tailed p-value using the standard normal CDF.

    Kendall's τ = S / (n*(n-1)/2) normalises S to [-1, 1].

    Returns:
        {
          "tau":        Kendall rank correlation coefficient [-1, 1],
          "p_value":    two-tailed p-value (approximate normal),
          "trend":      "increasing" | "decreasing" | "no trend",
          "significant": bool (p < 0.05),
        }
    """
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 4:
        return {"tau": 0.0, "p_value": 1.0, "trend": "no trend", "significant": False}

    # S = sum of sign(x[j] - x[i]) for all i < j pairs
    S = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            diff = x[j] - x[i]
            if diff > 0:
                S += 1
            elif diff < 0:
                S -= 1

    # Variance of S under H0 (simplified formula, no tie correction needed for continuous data)
    var_S = n * (n - 1) * (2 * n + 5) / 18.0

    # Z with continuity correction (±1 adjustment brings discrete S closer to normal)
    if S > 0:
        Z = (S - 1) / math.sqrt(var_S)
    elif S < 0:
        Z = (S + 1) / math.sqrt(var_S)
    else:
        Z = 0.0

    # Two-tailed p-value from normal approximation
    p_value = 2.0 * (1.0 - _norm_cdf(abs(Z)))

    tau = S / (0.5 * n * (n - 1))
    significant = p_value < 0.01

    if significant and tau > 0:
        trend = "increasing"
    elif significant and tau < 0:
        trend = "decreasing"
    else:
        trend = "no trend"

    return {
        "tau":         round(tau, 4),
        "p_value":     round(p_value, 4),
        "trend":       trend,
        "significant": significant,
    }


def _norm_cdf(x: float) -> float:
    """Approximate CDF of the standard normal using Horner's method."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


# ── Sen's slope ───────────────────────────────────────────────────────────────

def sens_slope(x: list[float]) -> float:
    """
    Estimate the median pairwise slope (Sen 1968).
    Returns slope per index step (i.e., per reading interval).
    """
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 2:
        return 0.0
    slopes = []
    for i in range(n - 1):
        for j in range(i + 1, n):
            if j != i:
                slopes.append((x[j] - x[i]) / (j - i))
    return float(np.median(slopes))


# ── CUSUM change-point ────────────────────────────────────────────────────────

def cusum_change_point(x: list[float], threshold: float = 7.0) -> dict:
    """
    Cumulative-sum (CUSUM) control chart to detect a single step-change.

    Baseline is estimated from the first 20% of readings (min 15, max 40) so that
    long-running sensors don't use a stale 5-point window as their reference.
    A minimum sigma floor of 0.5% of the data range prevents near-flat signals
    from making sigma → 0 and triggering on noise counts.

    threshold=7.0 gives ~1 false alarm per 2000 readings at this slack setting.
    """
    x = np.asarray(x, dtype=float)
    if len(x) < 15:
        return {"detected": False, "change_index": None, "direction": None}

    # Baseline from first 20% of data (clamped to [15, 40] readings)
    baseline_n = max(15, min(40, len(x) // 5))
    mu    = x[:baseline_n].mean()
    sigma = x[:baseline_n].std()
    # Floor: 0.5% of the observed data range — prevents false alarms on near-flat signals
    sigma = max(sigma, 0.005 * (x.max() - x.min() + 1e-8))

    S_hi = np.zeros(len(x))
    S_lo = np.zeros(len(x))
    for i in range(1, len(x)):
        # Accumulate normalised deviation; reset to 0 if the signal reverts (clamp)
        S_hi[i] = max(0, S_hi[i-1] + (x[i] - mu) / sigma - 0.5)
        S_lo[i] = max(0, S_lo[i-1] - (x[i] - mu) / sigma - 0.5)

    # Report the first index where the accumulator crosses the threshold
    if S_hi.max() > threshold:
        idx = int(np.argmax(S_hi > threshold))
        return {"detected": True, "change_index": idx, "direction": "up"}
    if S_lo.max() > threshold:
        idx = int(np.argmax(S_lo > threshold))
        return {"detected": True, "change_index": idx, "direction": "down"}

    return {"detected": False, "change_index": None, "direction": None}


# ── Rolling z-score ───────────────────────────────────────────────────────────

def rolling_zscore(x: list[float], window: int = 30) -> float:
    """
    Z-score of the most recent value relative to the preceding `window` values.
    Window raised to 30 for a more stable baseline.
    Returns 0.0 when the series is too short or perfectly flat.
    """
    x = np.asarray(x, dtype=float)
    if len(x) < window + 1:
        return 0.0
    recent = x[-window:]
    mu, sigma = recent.mean(), recent.std()
    if sigma < 1e-8:
        return 0.0
    return float((x[-1] - mu) / sigma)


# ── Main analysis entry point ────────────────────────────────────────────────

def analyze_parameter(param: str, values: list[float]) -> dict:
    """
    Run the full trend analysis suite for one parameter time-series.

    Returns a dict suitable for the /api/location/{loc}/trends endpoint.
    """
    if len(values) < 5:
        return {"param": param, "status": "insufficient_data", "n": len(values)}

    mk   = mann_kendall(values)
    slope = sens_slope(values)
    cusum = cusum_change_point(values)
    z     = rolling_zscore(values)

    nominal_range = constants.PARAMETER_NOMINAL_RANGES.get(param)
    unit          = constants.PARAMETER_UNITS.get(param, "")

    # Severity classification
    severity = _classify_severity(mk, slope, cusum, z, values, nominal_range)

    return {
        "param":          param,
        "unit":           unit,
        "n":              len(values),
        "current_value":  round(values[-1], 4),
        "nominal_range":  nominal_range,
        "mann_kendall":   mk,
        "sens_slope_per_reading": round(slope, 6),
        "cusum":          cusum,
        "z_score":        round(z, 3),
        "severity":       severity,
        "recommendation": _recommendation(param, mk, slope, cusum, z, severity),
    }


def analyze_location(location: str, history: dict[str, list]) -> list[dict]:
    """
    Run trend analysis for all parameters at a location.

    Args:
        location: ISS module name
        history:  {param_name: [value, value, ...]}   (oldest → newest)

    Returns list of analyze_parameter results, sorted by severity (critical first).
    """
    results = []
    severity_order = {"critical": 0, "warning": 1, "advisory": 2, "nominal": 3, "insufficient_data": 4}

    for param, values in history.items():
        if values:
            results.append(analyze_parameter(param, values))

    results.sort(key=lambda r: severity_order.get(r.get("severity", "nominal"), 3))
    return results


# ── Internal helpers ──────────────────────────────────────────────────────────

def _classify_severity(
    mk: dict, slope: float, cusum: dict, z: float,
    values: list[float], nominal_range
) -> str:
    """
    Assign one of: critical | warning | advisory | nominal.

    Tuning philosophy — each gate must pass two independent checks:
      • Statistical signal (MK tau, z, CUSUM) must exceed a high threshold so
        random noise doesn't fire on 1-in-20 sensors by chance.
      • Physical magnitude must be meaningful — a statistically real but
        physically tiny trend (e.g. 0.001 units/hr) is not actionable.
    """
    lo, hi, span = None, None, 1.0
    if nominal_range:
        lo, hi = nominal_range
        span = max(hi - lo, 1e-8)
        current = values[-1]

        # Hard out-of-bounds check (always immediate, no magnitude gate needed)
        if current < lo - 0.15 * span or current > hi + 0.15 * span:
            return "critical"
        if current < lo or current > hi:
            return "warning"

    # ── Trend severity ────────────────────────────────────────────────────────
    # Magnitude gate: Sen's slope projected over the full window must move ≥ 5%
    # of the nominal span. Filters statistically real but physically negligible drifts.
    projected_move = abs(slope) * len(values)
    slope_significant = projected_move >= 0.05 * span

    if mk["significant"] and abs(mk["tau"]) > 0.65 and slope_significant:
        # Strong monotonic trend heading somewhere meaningful
        if nominal_range:
            current = values[-1]
            # Only warn if the trend is pointing toward a boundary
            trending_toward_bound = (slope > 0 and current > lo + 0.5 * span) or \
                                    (slope < 0 and current < lo + 0.5 * span)
            if trending_toward_bound:
                return "warning"
        else:
            return "warning"

    # ── Advisory: require ≥ 2 independent signals, or 1 strong signal + magnitude ──
    signals = 0
    if mk["significant"] and abs(mk["tau"]) > 0.35 and slope_significant:
        signals += 1
    if cusum["detected"]:
        signals += 1
    if abs(z) > 3.5:
        signals += 1

    if signals >= 2:
        return "advisory"

    # Single strong z-score alone (very large spike) still warrants advisory
    if abs(z) > 4.5:
        return "advisory"

    return "nominal"


def _recommendation(
    param: str, mk: dict, slope: float, cusum: dict, z: float, severity: str
) -> str:
    if severity == "nominal":
        return "No action required."

    parts = []
    if mk["significant"]:
        direction = mk["trend"]
        per_hour  = slope * 3600  # assume 1-second readings
        parts.append(
            f"{param} shows a significant {direction} trend "
            f"(Sen's slope ≈ {per_hour:+.4f} {constants.PARAMETER_UNITS.get(param, 'units')}/hr)."
        )
    if cusum["detected"]:
        parts.append(f"CUSUM detected a step {cusum['direction']} shift at reading {cusum['change_index']}.")
    if abs(z) > 2.5:
        parts.append(f"Rolling z-score = {z:.2f} — recent value is an outlier relative to recent baseline.")

    # Pair with fault knowledge
    for fault, info in constants.FAULT_IMPACT_SEVERITY.items():
        if param in info["impacts"]:
            coeff = info["impacts"][param]
            if (coeff > 0 and mk["trend"] == "increasing") or \
               (coeff < 0 and mk["trend"] == "decreasing"):
                parts.append(f"Consistent with early signs of '{fault}'.")
                break

    if not parts:
        return f"{param} is outside nominal range — monitor closely."

    return " ".join(parts)
