"""
LSTM-based failure predictor for ECLSS parameters.

Architecture:
    Input  : (batch, seq_len=60, n_features=20)  — sliding window of readings
    LSTM   : 3 layers, hidden_size=128, dropout=0.2
    Attn   : Multi-head self-attention on LSTM outputs
    Heads  :
        failure_head → scalar in [0,1]  (probability of fault within window)
        rul_head     → scalar ≥ 0       (predicted remaining useful life, hours)

Saved as:
    models/lstmModel.pt
        {"model_state": state_dict, "param_order": list, "seq_len": int,
         "hidden_size": int, "num_layers": int, "scaler_mean": ndarray,
         "scaler_std": ndarray}
"""

import numpy as np

try:
    import torch
    import torch.nn as nn
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False

import constants

SEQ_LEN     = 60
HIDDEN_SIZE = 128
NUM_LAYERS  = 3
DROPOUT     = 0.2
N_HEADS     = 8   # attention heads


# ── Model definition ──────────────────────────────────────────────────────────

if TORCH_AVAILABLE:
    class ECLSSLSTMPredictor(nn.Module):
        def __init__(
            self,
            input_size: int = len(constants.PARAMETER_NOMINAL_RANGES),
            hidden_size: int = HIDDEN_SIZE,
            num_layers: int = NUM_LAYERS,
            dropout: float = DROPOUT,
        ):
            super().__init__()
            self.lstm = nn.LSTM(
                input_size=input_size,
                hidden_size=hidden_size,
                num_layers=num_layers,
                dropout=dropout,
                batch_first=True,
            )
            self.attn = nn.MultiheadAttention(
                hidden_size, num_heads=N_HEADS, batch_first=True
            )
            self.failure_head = nn.Sequential(
                nn.Linear(hidden_size, 64),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(64, 1),
                nn.Sigmoid(),
            )
            self.rul_head = nn.Sequential(
                nn.Linear(hidden_size, 64),
                nn.ReLU(),
                nn.Linear(64, 1),
                nn.ReLU(),  # RUL ≥ 0
            )

        def forward(self, x):
            lstm_out, _ = self.lstm(x)                       # (B, T, H)
            attn_out, _ = self.attn(lstm_out, lstm_out, lstm_out)  # (B, T, H)
            context = attn_out[:, -1, :]                     # last timestep
            failure_prob = self.failure_head(context).squeeze(-1)
            rul_hours    = self.rul_head(context).squeeze(-1)
            return failure_prob, rul_hours


# ── Inference wrapper ─────────────────────────────────────────────────────────

class LSTMPipeline:
    """
    Load and run inference with the trained LSTM model.
    Maintains a per-location rolling window buffer of the last SEQ_LEN readings.
    """

    def __init__(self, model_path: str = "models/lstmModel.pt"):
        self.model = None
        self.param_order = None
        self.scaler_mean = None
        self.scaler_std  = None
        self.seq_len     = SEQ_LEN
        self.enabled     = False

        # Per-location ring buffer: deque of SEQ_LEN dicts
        self._buffers: dict[str, list] = {loc: [] for loc in constants.LOCATIONS}

        self._load(model_path)

    def _load(self, path: str):
        if not TORCH_AVAILABLE:
            print("[LSTM] PyTorch not installed — LSTM disabled.")
            return
        try:
            import torch
            ckpt = torch.load(path, map_location="cpu", weights_only=False)
            self.param_order = ckpt["param_order"]
            self.scaler_mean = np.array(ckpt["scaler_mean"])
            self.scaler_std  = np.array(ckpt["scaler_std"])
            self.seq_len     = ckpt.get("seq_len", SEQ_LEN)

            self.model = ECLSSLSTMPredictor(
                input_size=len(self.param_order),
                hidden_size=ckpt.get("hidden_size", HIDDEN_SIZE),
                num_layers=ckpt.get("num_layers", NUM_LAYERS),
            )
            self.model.load_state_dict(ckpt["model_state"])
            self.model.eval()
            self.enabled = True
            print(f"[LSTM] Model loaded from {path}")
        except FileNotFoundError:
            print(f"[LSTM] No model at {path} — run scripts/train_lstm.py first.")
        except Exception as e:
            print(f"[LSTM] Failed to load: {e}")

    def push(self, location: str, reading: dict):
        """Add a new reading to the location's rolling buffer."""
        buf = self._buffers.setdefault(location, [])
        buf.append(reading)
        if len(buf) > self.seq_len:
            buf.pop(0)

    def clear_buffer(self, location: str = None):
        """Clear the rolling buffer. Clears all locations if location is None."""
        if location is None:
            for loc in self._buffers:
                self._buffers[loc] = []
        else:
            self._buffers[location] = []

    def predict(self, location: str) -> dict | None:
        """
        Run inference on the current rolling window for a location.
        Returns {"failure_prob": float, "rul_hours": float} or None if buffer
        isn't full yet or model is disabled.
        """
        if not self.enabled:
            return None
        buf = self._buffers.get(location, [])
        if len(buf) < self.seq_len:
            return None

        import torch
        X = np.array([
            [(row.get(p, 0.0) - self.scaler_mean[i]) / (self.scaler_std[i] + 1e-8)
             for i, p in enumerate(self.param_order)]
            for row in buf[-self.seq_len:]
        ], dtype=np.float32)  # (seq_len, features)

        with torch.no_grad():
            t = torch.tensor(X).unsqueeze(0)  # (1, seq_len, features)
            fp, rul = self.model(t)

        return {
            "failure_prob": float(fp.item()),
            "rul_hours":    float(rul.item()),
        }
