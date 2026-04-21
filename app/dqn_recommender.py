"""
DQN-based action recommender for ECLSS fault remediation.

Loaded at server startup alongside ML/LSTM pipelines.
Call .recommend() with the current system state to get an action recommendation.
"""

import numpy as np
import torch
import torch.nn as nn
from typing import Optional

from . import constants

N_PARAMS  = len(constants.PARAMETER_NOMINAL_RANGES)   # 20
N_FAULTS  = len(constants.FAULT_IMPACT_SEVERITY)      # 8
N_SCALARS = 4   # anomaly_score_proxy, if_is_anomaly, failure_prob, rul_norm
STATE_SIZE = N_PARAMS + N_FAULTS + N_SCALARS           # 32
N_ACTIONS  = len(constants.ACTIONS_TO_TAKE)            # 11

# Direct fault → remediation action lookup (derived from constants).
# Used as a high-confidence bypass when RF is certain — more reliable than
# relying on the DQN to learn the same fixed mapping.
_FAULT_TO_ACTION: dict[str, str] = {
    fault: action for action, fault in constants.ACTIONS_TO_FAULT.items()
}
RF_BYPASS_THRESHOLD = 0.92   # RF confidence above which we skip the DQN (↑ from 0.85)


class DQNNet(nn.Module):
    def __init__(self, state_size: int = STATE_SIZE, action_size: int = N_ACTIONS):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(state_size, 256),
            nn.LayerNorm(256),
            nn.ReLU(),
            nn.Linear(256, 128),
            nn.ReLU(),
            nn.Linear(128, action_size),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class DQNRecommender:
    """Wraps a trained DQN and provides action recommendations at runtime."""

    def __init__(self, model_path: str = "models/dqnModel.pt"):
        self.enabled     = False
        self.param_order = list(constants.PARAMETER_NOMINAL_RANGES.keys())
        self.faults      = list(constants.FAULT_IMPACT_SEVERITY.keys())
        self.actions     = constants.ACTIONS_TO_TAKE

        try:
            ckpt = torch.load(model_path, map_location="cpu", weights_only=True)
            ss   = ckpt.get("state_size", STATE_SIZE)
            ac   = ckpt.get("action_size", N_ACTIONS)
            self.model = DQNNet(state_size=ss, action_size=ac)
            self.model.load_state_dict(ckpt["model_state"])
            self.model.eval()
            self.scaler_mean = np.array(ckpt["scaler_mean"], dtype=np.float32)
            self.scaler_std  = np.array(ckpt["scaler_std"],  dtype=np.float32)
            self.enabled = True
            print(f"[DQN] Model loaded from {model_path}")
        except FileNotFoundError:
            print(f"[DQN] No model at {model_path} — run scripts/train_dqn.py first")
        except Exception as e:
            print(f"[DQN] Load error: {e}")

    def _encode(
        self,
        sensor_data:       dict,
        anomaly_score:     float,
        if_label:          int,
        rf_classification: Optional[dict],
        failure_prob:      float,
        rul_hours:         float,
    ) -> np.ndarray:
        """Build the 32-dimensional state vector fed to the DQN.

        Layout (STATE_SIZE = 32):
          [0:20]  sensor values — z-scored with training-set mean/std
          [20:28] RF per-fault probabilities — 0.0 when IF nominal (no RF output)
          [28:32] scalar context:
                    [28] anomaly score proxy: clip(-score/10, 0, 1) — larger = more anomalous
                    [29] IF anomaly flag: 1.0 if label == -1 else 0.0
                    [30] failure_prob from LSTM (or RF-proxy when LSTM buffer filling)
                    [31] RUL normalised: clip(rul, 0, 200) / 200

        The z-scoring uses the same scaler trained alongside the DQN so the model
        sees the same distribution it was trained on."""
        # Z-score sensor readings to match training distribution
        sensors = np.array(
            [sensor_data.get(p, 0.0) for p in self.param_order], dtype=np.float32
        )
        sensors = (sensors - self.scaler_mean) / (self.scaler_std + 1e-8)

        # Align RF probabilities to the fixed fault ordering from training
        rf_probs = np.zeros(N_FAULTS, dtype=np.float32)
        if rf_classification:
            for i, fault in enumerate(self.faults):
                rf_probs[i] = rf_classification.get(fault, 0.0)

        # Scalar context: normalise each to [0, 1] range the DQN expects
        scalars = np.array([
            float(np.clip(-anomaly_score / 10.0, 0.0, 1.0)),  # IF decision score (negative = anomalous)
            float(if_label == -1),                              # binary anomaly flag
            float(np.clip(failure_prob, 0.0, 1.0)),            # LSTM failure probability
            float(np.clip(rul_hours, 0.0, 200.0) / 200.0),    # RUL capped at 200 h and normalised
        ], dtype=np.float32)

        return np.concatenate([sensors, rf_probs, scalars])

    def recommend(
        self,
        sensor_data:       dict,
        anomaly_score:     float        = 0.0,
        if_label:          int          = 1,
        rf_classification: Optional[dict] = None,
        failure_prob:      float        = 0.0,
        rul_hours:         float        = 200.0,
    ) -> dict:
        """
        Returns the recommended action and Q-values for all 11 actions.

        High-confidence path: when IF flags anomalous and RF >= RF_BYPASS_THRESHOLD
        confident on a fault, return the known remediation action directly.
        The DQN handles genuinely ambiguous cases (low RF confidence, no signal).
        Falls back to 'No Action Needed' if the model is not loaded.
        """
        # RF high-confidence bypass — more reliable than DQN for clear-cut faults
        if if_label == -1 and rf_classification:
            top_fault, top_prob = max(rf_classification.items(), key=lambda x: x[1])
            if top_prob >= RF_BYPASS_THRESHOLD:
                action = _FAULT_TO_ACTION.get(top_fault)
                if action and action in self.actions:
                    return {
                        "action":       action,
                        "action_index": self.actions.index(action),
                        "confidence":   float(top_prob),
                        "q_values":     None,
                    }

        if not self.enabled:
            return {
                "action":       "No Action Needed",
                "action_index": 0,
                "confidence":   0.0,
                "q_values":     None,
            }

        state  = self._encode(sensor_data, anomaly_score, if_label,
                               rf_classification, failure_prob, rul_hours)
        tensor = torch.tensor(state).unsqueeze(0)   # (1, STATE_SIZE) — batch dim for the linear layers
        with torch.no_grad():
            q = self.model(tensor).squeeze(0).numpy()   # raw Q-values for each action

        idx  = int(np.argmax(q))    # greedy action selection

        # Softmax over Q-values gives a pseudo-probability distribution for the "confidence" metric.
        # Subtracting max before exp prevents overflow while leaving argmax unchanged.
        e    = np.exp(q - q.max())
        prob = e / e.sum()

        return {
            "action":       self.actions[idx],
            "action_index": idx,
            "confidence":   float(prob[idx]),
            "q_values":     {self.actions[i]: float(q[i]) for i in range(len(self.actions))},
        }
