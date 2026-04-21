"""
Train the LSTM failure predictor + RUL estimator.

Run from the AURA/ directory:
    python3 scripts/train_lstm.py

Requires:  pip install torch

Outputs:
    models/lstmModel.pt
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader, random_split

from app import constants
from app.data_generator import SensorDataGenerator
from app.lstm_predictor import ECLSSLSTMPredictor, SEQ_LEN, HIDDEN_SIZE, NUM_LAYERS

# ── Config ────────────────────────────────────────────────────────────────────
SEQ_NOMINAL_PER_LOC = 400    # nominal sequences per location
SEQ_FAULT_PER_FAULT = 250    # fault sequences per fault type
BATCH_SIZE          = 128
EPOCHS              = 80
LR                  = 5e-4
WEIGHT_DECAY        = 1e-4
VAL_SPLIT           = 0.15
PATIENCE            = 12     # early stopping patience
OUTPUT_PATH         = "models/lstmModel.pt"
DEVICE              = "cuda" if torch.cuda.is_available() else "cpu"

print(f"Device: {DEVICE}")

# ── Dataset ───────────────────────────────────────────────────────────────────

class ECLSSSequenceDataset(Dataset):
    """
    Each item: (X_seq, y_fail, y_rul)
      X_seq : (SEQ_LEN, n_features) float32
      y_fail: scalar float32 in {0, 1}
      y_rul : scalar float32 (hours; 1000 for nominal)
    """
    def __init__(self, sequences, param_order, scaler_mean, scaler_std):
        self.param_order  = param_order
        self.scaler_mean  = scaler_mean
        self.scaler_std   = scaler_std
        self.X, self.yf, self.yr = [], [], []

        for seq in sequences:
            # seq is a list of SEQ_LEN dicts: {data, anomaly, rul_hours}
            x = np.array([
                [(s["data"].get(p, 0.0) - scaler_mean[i]) / (scaler_std[i] + 1e-8)
                 for i, p in enumerate(param_order)]
                for s in seq
            ], dtype=np.float32)
            last = seq[-1]
            # y_fail = 1 whenever the fault has started (rul_hours is set),
            # not just past 50% progression.  This trains the LSTM to detect
            # any fault condition, giving recall on all 8 fault types.
            yf = 1.0 if last["rul_hours"] is not None else 0.0
            yr = float(last["rul_hours"]) if last["rul_hours"] is not None else 1000.0

            self.X.append(x)
            self.yf.append(yf)
            self.yr.append(yr)

    def __len__(self):
        return len(self.X)

    def __getitem__(self, idx):
        return (
            torch.tensor(self.X[idx]),
            torch.tensor(self.yf[idx], dtype=torch.float32),
            torch.tensor(self.yr[idx],  dtype=torch.float32),
        )


def make_sequences(raw_records: list, seq_len: int) -> list:
    """Slide a window of seq_len over a list of raw records."""
    return [raw_records[i:i + seq_len] for i in range(len(raw_records) - seq_len + 1)]


# ── Generate data ─────────────────────────────────────────────────────────────

print("Generating training sequences ...")
gen = SensorDataGenerator(seed=1234, step_seconds=60.0)  # 1-min steps for training
param_order = list(constants.PARAMETER_NOMINAL_RANGES.keys())
faults = list(constants.FAULT_IMPACT_SEVERITY.keys())

all_sequences = []

# Nominal sequences (from multiple "locations" for variety)
for loc in constants.LOCATIONS:
    records = gen.generate_sequence(
        n=SEQ_NOMINAL_PER_LOC + SEQ_LEN,
        fault=None,
        location=loc,
    )
    all_sequences.extend(make_sequences(records, SEQ_LEN))

# Fault sequences (each fault, each location)
for fault in faults:
    for loc in constants.LOCATIONS:   # all 7 locations for better generalisation
        n_total = SEQ_FAULT_PER_FAULT + SEQ_LEN
        fault_start = n_total // 3  # fault begins 1/3 of the way through
        records = gen.generate_sequence(
            n=n_total,
            fault=fault,
            fault_start=fault_start,
            location=loc,
        )
        all_sequences.extend(make_sequences(records, SEQ_LEN))

print(f"Total sequences: {len(all_sequences):,}")
print(f"  Anomalous (last step): {sum(1 for s in all_sequences if s[-1]['anomaly']==1):,}")
print(f"  Nominal   (last step): {sum(1 for s in all_sequences if s[-1]['anomaly']==0):,}")

# ── Compute normalisation stats from all readings ─────────────────────────────
all_readings = [step["data"] for seq in all_sequences for step in seq]
X_flat = np.array([[r.get(p, 0.0) for p in param_order] for r in all_readings])
scaler_mean = X_flat.mean(axis=0).astype(np.float32)
scaler_std  = X_flat.std(axis=0).astype(np.float32)

# ── Build DataLoaders ─────────────────────────────────────────────────────────
dataset   = ECLSSSequenceDataset(all_sequences, param_order, scaler_mean, scaler_std)
n_val     = int(len(dataset) * VAL_SPLIT)
n_train   = len(dataset) - n_val
train_ds, val_ds = random_split(dataset, [n_train, n_val],
                                generator=torch.Generator().manual_seed(42))

train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True,  num_workers=0)
val_loader   = DataLoader(val_ds,   batch_size=BATCH_SIZE, shuffle=False, num_workers=0)

# ── Model ─────────────────────────────────────────────────────────────────────
model = ECLSSLSTMPredictor(
    input_size=len(param_order),
    hidden_size=HIDDEN_SIZE,
    num_layers=NUM_LAYERS,
).to(DEVICE)

optimizer  = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)
scheduler  = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=EPOCHS)
criterion_cls = nn.BCELoss()
criterion_rul = nn.MSELoss()

# ── Training loop ─────────────────────────────────────────────────────────────
print(f"\nTraining for {EPOCHS} epochs (early stopping patience={PATIENCE}) ...")
best_val_loss = float("inf")
epochs_no_improve = 0

for epoch in range(1, EPOCHS + 1):
    # Train
    model.train()
    train_loss = 0.0
    for X, yf, yr in train_loader:
        X, yf, yr = X.to(DEVICE), yf.to(DEVICE), yr.to(DEVICE)
        optimizer.zero_grad()
        pred_fp, pred_rul = model(X)
        # Masked RUL loss: only train on fault sequences (yr < 999).
        # Training on the nominal placeholder (yr=1000) dominates the MSE
        # gradient by ~27000:1 and prevents the model from learning RUL.
        fault_mask = yr < 999.0
        if fault_mask.sum() > 0:
            rul_loss = criterion_rul(pred_rul[fault_mask], yr[fault_mask])
        else:
            rul_loss = torch.tensor(0.0, device=DEVICE)
        loss = criterion_cls(pred_fp, yf) + 0.01 * rul_loss
        loss.backward()
        nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        train_loss += loss.item()
    train_loss /= len(train_loader)

    # Validate
    model.eval()
    val_loss, val_correct, val_total = 0.0, 0, 0
    val_tp, val_fp, val_fn = 0, 0, 0
    with torch.no_grad():
        for X, yf, yr in val_loader:
            X, yf, yr = X.to(DEVICE), yf.to(DEVICE), yr.to(DEVICE)
            pred_fp, pred_rul = model(X)
            fault_mask_v = yr < 999.0
            rul_loss_v = criterion_rul(pred_rul[fault_mask_v], yr[fault_mask_v]) if fault_mask_v.sum() > 0 else torch.tensor(0.0)
            loss = criterion_cls(pred_fp, yf) + 0.01 * rul_loss_v
            val_loss += loss.item()
            preds = (pred_fp > 0.5).float()
            val_correct += (preds == yf).sum().item()
            val_total += len(yf)
            val_tp += ((preds == 1) & (yf == 1)).sum().item()
            val_fp += ((preds == 1) & (yf == 0)).sum().item()
            val_fn += ((preds == 0) & (yf == 1)).sum().item()
    val_loss /= len(val_loader)
    val_acc  = val_correct / val_total
    precision = val_tp / (val_tp + val_fp + 1e-8)
    recall    = val_tp / (val_tp + val_fn + 1e-8)
    f1        = 2 * precision * recall / (precision + recall + 1e-8)

    scheduler.step()

    if epoch % 5 == 0 or epoch == 1:
        print(f"  Epoch {epoch:3d}/{EPOCHS} | "
              f"train={train_loss:.4f} | "
              f"val={val_loss:.4f} | "
              f"acc={val_acc:.2%} | "
              f"F1={f1:.3f} | "
              f"P={precision:.3f} R={recall:.3f}")

    if val_loss < best_val_loss:
        best_val_loss = val_loss
        epochs_no_improve = 0
        torch.save({
            "model_state":  model.state_dict(),
            "param_order":  param_order,
            "seq_len":      SEQ_LEN,
            "hidden_size":  HIDDEN_SIZE,
            "num_layers":   NUM_LAYERS,
            "scaler_mean":  scaler_mean.tolist(),
            "scaler_std":   scaler_std.tolist(),
        }, OUTPUT_PATH)
    else:
        epochs_no_improve += 1
        if epochs_no_improve >= PATIENCE:
            print(f"\n  Early stopping at epoch {epoch} (no improvement for {PATIENCE} epochs)")
            break

print(f"\nBest val_loss: {best_val_loss:.4f}")
print(f"Saved -> {OUTPUT_PATH}")
print("Done.")
