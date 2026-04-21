"""
Train the Random Forest fault classifier.

Run from the AURA/ directory:
    python3 scripts/train_random_forest.py

Outputs:
    models/randomForestModel.joblib  (bare sklearn RandomForestClassifier)

The RF is trained only on fault data — it assumes the Isolation Forest
has already flagged the input as anomalous.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, confusion_matrix
from app import constants
from app.data_generator import SensorDataGenerator

# ── Config ────────────────────────────────────────────────────────────────────
N_PER_TRAJ   = 300    # samples per trajectory — matches test horizon (5 h at 60 s/step)
N_TRAJ       = 10    # independent trajectories per fault (each reset to drift=0)
N_ESTIMATORS = 500
OUTPUT_PATH  = "models/randomForestModel.joblib"

# ── Generate labeled fault data ───────────────────────────────────────────────
# Each trajectory is an independent 5-hour fault run starting from drift=0.
# Multiple trajectories give diverse noise realisations of the same fault type
# while exactly matching the test distribution (also 300 samples = 5 h).
print("Generating fault training data ...")
gen = SensorDataGenerator(seed=0, step_seconds=60.0)  # 1-min steps → realistic fault drift magnitudes
param_order = list(constants.PARAMETER_NOMINAL_RANGES.keys())
faults = list(constants.FAULT_IMPACT_SEVERITY.keys())

X_all, y_all = [], []
for fault in faults:
    print(f"  {fault} ({N_TRAJ} x {N_PER_TRAJ} samples)")
    for _ in range(N_TRAJ):
        rows, labels = gen.generate_fault_batch(fault, n_per_fault=N_PER_TRAJ)
        for r, lbl in zip(rows, labels):
            X_all.append([r[p] for p in param_order])
            y_all.append(lbl)

X = np.array(X_all)
y = np.array(y_all)

print(f"\nTotal samples: {len(X)}")
print(f"Classes: {faults}")

# ── Split ─────────────────────────────────────────────────────────────────────
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)

# ── Train ─────────────────────────────────────────────────────────────────────
print(f"\nTraining RandomForest (n_estimators={N_ESTIMATORS}) ...")
model = RandomForestClassifier(
    n_estimators=N_ESTIMATORS,
    max_depth=None,
    min_samples_split=4,
    min_samples_leaf=2,
    max_features="sqrt",     # standard best-practice for RF classification
    class_weight="balanced", # handles any remaining class imbalance
    n_jobs=-1,
    random_state=42,
)
model.fit(X_train, y_train)

# ── Evaluate ──────────────────────────────────────────────────────────────────
print("\nClassification report (test set):")
y_pred = model.predict(X_test)
print(classification_report(y_test, y_pred, target_names=faults))

acc = (y_pred == y_test).mean()
print(f"Overall accuracy: {acc:.2%}")

# Top-5 feature importances
fi = model.feature_importances_
top5 = np.argsort(fi)[-5:][::-1]
print("\nTop 5 important parameters:")
for i in top5:
    print(f"  {param_order[i]:<45} {fi[i]:.4f}")

# ── Save ──────────────────────────────────────────────────────────────────────
joblib.dump(model, OUTPUT_PATH, compress=3)
print(f"\nSaved -> {OUTPUT_PATH}")
print("Done.")
