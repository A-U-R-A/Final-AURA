import numpy as np
import joblib
from pathlib import Path
import constants


class MLPipeline:
    """
    Wraps the trained Isolation Forest + Random Forest models.

    The IF model joblib file is expected to contain:
        {"model": sklearn.IsolationForest, "scaler": sklearn.StandardScaler,
         "param_order": list[str]}

    The RF model joblib file is expected to be a bare sklearn classifier
    with a .classes_ attribute.
    """

    def __init__(
        self,
        if_path: str = constants.IF_MODEL_PATH,
        rf_path: str = constants.RF_MODEL_PATH,
    ):
        self.if_model = None
        self.rf_model = None
        self.scaler = None
        self.param_order = None
        self.enabled = False

        self._load(if_path, rf_path)

    def _load(self, if_path: str, rf_path: str):
        """Load both models at startup. Sets self.enabled = True only if both load successfully.
        If either model file is missing the server starts normally and returns nominal (1, None)
        from predict() — the frontend degrades gracefully when ml_enabled is False."""
        try:
            if_data = joblib.load(if_path)
            if isinstance(if_data, dict):
                # New format: bundle includes model, StandardScaler, and param ordering
                self.if_model = if_data["model"]
                self.scaler = if_data.get("scaler")
                self.param_order = if_data.get("param_order", list(constants.PARAMETER_NOMINAL_RANGES.keys()))
            else:
                # Legacy: bare model without scaler (pre-v2 training scripts)
                self.if_model = if_data
                self.param_order = list(constants.PARAMETER_NOMINAL_RANGES.keys())

            self.rf_model = joblib.load(rf_path)
            # Force single-threaded inference — prevents joblib from opening
            # worker processes that inherit and leak SQLite file descriptors.
            if hasattr(self.if_model, "n_jobs"):
                self.if_model.n_jobs = 1
            if hasattr(self.rf_model, "n_jobs"):
                self.rf_model.n_jobs = 1
            self.enabled = True
            print(f"[ML] Models loaded — IF params: {len(self.param_order)}, "
                  f"RF classes: {list(self.rf_model.classes_)}")
        except FileNotFoundError as e:
            print(f"[ML] Model file not found ({e}). Predictions disabled.")
        except Exception as e:
            print(f"[ML] Failed to load models: {e}. Predictions disabled.")

    def predict(self, sensor_dict: dict) -> tuple[int, dict | None]:
        """
        Two-stage IF → RF inference pipeline:

        Stage 1 — Isolation Forest (anomaly detection):
            Scores the reading against the learned nominal distribution.
            Returns 1 (nominal) or -1 (anomalous). Uses StandardScaler-transformed
            features so the IF operates in a normalised feature space.

        Stage 2 — Random Forest (fault classification):
            Runs ONLY when IF flags anomalous (-1). Returns per-class probabilities
            for all known fault types. Uses raw (unscaled) features because the RF
            was trained that way — DO NOT scale before passing to RF.

        Returns:
            (if_label, rf_classification)
            if_label: 1 = nominal, -1 = anomalous
            rf_classification: {fault_name: probability} or None if nominal
        """
        if not self.enabled:
            return 1, None

        try:
            # Build feature vector aligned to training parameter order (missing params → 0)
            X_raw = np.array([[
                sensor_dict.get(p, 0.0) for p in self.param_order
            ]])

            # IF uses scaled features; RF uses raw (training setup difference)
            X_input = self.scaler.transform(X_raw) if self.scaler else X_raw

            if_label = int(self.if_model.predict(X_input)[0])

            rf_classification = None
            if if_label == -1:
                probs = self.rf_model.predict_proba(X_raw)[0]
                rf_classification = {
                    cls: round(float(prob), 4)
                    for cls, prob in zip(self.rf_model.classes_, probs)
                }

            return if_label, rf_classification

        except Exception as e:
            print(f"[ML] Inference error: {e}")
            return 1, None

    def anomaly_score(self, sensor_dict: dict) -> float:
        """Return the raw IF decision function score (more negative = more anomalous).
        Used by DQNRecommender as one of the scalar inputs to the state vector."""
        if not self.enabled or self.if_model is None:
            return 0.0
        try:
            X_raw = np.array([[
                sensor_dict.get(p, 0.0) for p in self.param_order
            ]])
            X_input = self.scaler.transform(X_raw) if self.scaler else X_raw
            return float(self.if_model.decision_function(X_input)[0])
        except Exception:
            return 0.0
