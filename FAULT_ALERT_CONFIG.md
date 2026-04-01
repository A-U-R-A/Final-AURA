# Fault Alert Configuration

This document describes the per-fault alert tuning system in AURA.

## Overview

Each fault type has customizable alert sensitivity settings defined in `constants.FAULT_ALERT_CONFIG`. This allows different faults to trigger alerts at different speeds based on their operational urgency.

## Configuration Structure

Each fault entry has two parameters:

```python
FAULT_ALERT_CONFIG = {
    "Fault Name": {
        "min_consecutive": N,    # Number of consecutive anomalous readings before alert
        "cooldown_seconds": S,   # Minimum seconds between repeated alerts for this fault
    },
    ...
}
```

### Parameters

- **`min_consecutive`**: How many consecutive ticks (at 1Hz = 1 second per tick) the Isolation Forest must flag as anomalous before an alert fires. Higher values = less sensitive (fewer alerts), lower values = more sensitive (more alerts).
  - Typical range: 10-50 ticks
  - Fast faults (NH3 Leak, O2 Leak): 10-15 ticks
  - Slow faults (Filter Saturation, Water Processor): 40-50 ticks

- **`cooldown_seconds`**: After an alert fires for this fault at a location, how long must elapse before another alert of the same type can fire at that location.
  - Typical range: 300-1200 seconds (5-20 minutes)
  - Urgent/fast faults: 300 sec (5 min)
  - Slow/degradation faults: 900-1200 sec (15-20 min)

## Current Configuration

| Fault | min_consecutive | cooldown_seconds | Rationale |
|-------|-----------------|------------------|-----------|
| NH3 Coolant Leak | 10 | 300 | Fast, dangerous—early detection |
| O2 Leak | 15 | 300 | Pressure drops quickly |
| CO2 Scrubber Failure | 15 | 300 | CO2 rises fast—urgent |
| Cabin Leak | 20 | 300 | Moderate urgency |
| CHX Failure | 25 | 450 | Humidity/temp gradual |
| O2 Generator Failure | 30 | 600 | Gradual degradation |
| Water Processor Failure | 40 | 900 | Very slow purity drift |
| Trace Contaminant Filter Saturation | 50 | 1200 | Extremely slow buildup |

## How to Tune

To adjust alert sensitivity for a specific fault:

1. Open `constants.py`
2. Find the fault in `FAULT_ALERT_CONFIG`
3. Modify `min_consecutive` and/or `cooldown_seconds`:
   - **More sensitive** (fewer reads needed): Lower `min_consecutive`
   - **Less sensitive** (more reads needed): Raise `min_consecutive`
   - **More frequent repeats**: Lower `cooldown_seconds`
   - **Fewer repeats**: Raise `cooldown_seconds`
4. Restart the server: `python main.py`

### Example: Make O2 Leak alerts fire faster

**Before:**
```python
"O2 Leak": {
    "min_consecutive": 15,
    "cooldown_seconds": 300,
},
```

**After (fire on 10 consecutive anomalies instead of 15):**
```python
"O2 Leak": {
    "min_consecutive": 10,  # ← Changed from 15
    "cooldown_seconds": 300,
},
```

## Interaction with Main Loop

In `main.py`, the generation loop:

1. Gets the latest RF classification (which fault was detected)
2. Looks up the fault in `constants.FAULT_ALERT_CONFIG`
3. Uses the per-fault `min_consecutive` and `cooldown_seconds` when deciding to fire an alert
4. Falls back to sensible defaults (30 ticks, 300 sec) if the fault is not in the config

## Testing Alert Sensitivity

To verify alerts are working as configured:

1. Start the server: `python main.py`
2. Open the UI at `http://localhost:8000`
3. Go to **Digital Twin** tab → **Fault Injection**
4. Select a location and fault, then click **Inject Fault**
5. Wait for the Isolation Forest to flag consecutive anomalies
6. Check the **Alerts** tab to see when the alert fires (should be after `min_consecutive` ticks)
7. Adjust the config values and repeat to find optimal sensitivity

## Notes

- Alert counts are **per-location**, not global (each ISS module has independent alert tracking)
- Cooldown prevents alert spam when a fault is stable and continuously detected
- The initial LSTM alert logic (for crew actions) uses different thresholds in `constants.py` (see `LSTM_ALERT_FAILURE_PROB`, etc.)
