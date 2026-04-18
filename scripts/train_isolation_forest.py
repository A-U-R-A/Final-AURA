"""
Train the Isolation Forest anomaly detector.

Run from the AURA/ directory:
    python3 scripts/train_isolation_forest.py

Outputs:
    models/isolationForestModel.joblib
        {"model": IsolationForest, "scaler": StandardScaler, "param_order": list}
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import joblib
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import confusion_matrix
from app import constants
from app.data_generator import SensorDataGenerator

# ── Config ────────────────────────────────────────────────────────────────────
N_NOMINAL      = 20_000  # training samples (all healthy) — larger = tighter boundary
N_TEST_NOMINAL = 2_000   # held-out healthy test samples
N_TEST_ANOMALY = 400     # anomalous test samples
CONTAMINATION  = 0.02    # tuned: FPR~2%, TPR~79% at 60-sec fault step (see validate_models.py)
N_ESTIMATORS   = 300
OUTPUT_PATH    = "models/isolationForestModel.joblib"

# ── Generate training data ────────────────────────────────────────────────────
print("Generating nominal training data ...")
gen = SensorDataGenerator(seed=42)
param_order = list(constants.PARAMETER_NOMINAL_RANGES.keys())

# Seed mission clock so training spans a full 24-hour circadian cycle,
# preventing the IF from only learning hour-0 distributions.
gen._mission_seconds = 0.0
train_rows = gen.generate_nominal_batch(N_NOMINAL)
X_train = np.array([[r[p] for p in param_order] for r in train_rows])

# ── Scale ─────────────────────────────────────────────────────────────────────
print("Fitting StandardScaler ...")
scaler = StandardScaler()
X_train_scaled = scaler.fit_transform(X_train)

# ── Train ─────────────────────────────────────────────────────────────────────
print(f"Training IsolationForest (n_estimators={N_ESTIMATORS}) ...")
model = IsolationForest(
    n_estimators=N_ESTIMATORS,
    max_samples="auto",      # auto selects min(256, n_samples) — faster and often better
    contamination=CONTAMINATION,
    max_features=1.0,
    bootstrap=False,
    n_jobs=-1,
    random_state=42,
)
model.fit(X_train_scaled)

# ── Evaluate ──────────────────────────────────────────────────────────────────
print("\nEvaluating ...")
test_nominal_rows = gen.generate_nominal_batch(N_TEST_NOMINAL)
X_test_nom = scaler.transform(
    np.array([[r[p] for p in param_order] for r in test_nominal_rows])
)
preds_nom = model.predict(X_test_nom)
fpr = (preds_nom == -1).mean()
print(f"  False positive rate (nominal data): {fpr:.2%}  (target <= 1%)")

# Test on fault data
print("  Generating fault test samples ...")
X_fault, y_fault = [], []
for fault in list(constants.FAULT_IMPACT_SEVERITY.keys()):
    fault_rows, _ = gen.generate_fault_batch(fault, n_per_fault=50)
    for r in fault_rows:
        X_fault.append([r[p] for p in param_order])
        y_fault.append(-1)  # all should be flagged as anomalous

X_fault_scaled = scaler.transform(np.array(X_fault))
preds_fault = model.predict(X_fault_scaled)
tpr = (preds_fault == -1).mean()
print(f"  True positive rate (fault data):    {tpr:.2%}  (target >= 50%)")
print(f"  Note: IF is unsupervised — low TPR is expected on early-stage faults.")

# ── Save ──────────────────────────────────────────────────────────────────────
payload = {
    "model":       model,
    "scaler":      scaler,
    "param_order": param_order,
}
joblib.dump(payload, OUTPUT_PATH, compress=3)
print(f"\nSaved -> {OUTPUT_PATH}")
print("Done.")
