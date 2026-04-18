"""
Train the DQN action recommender.

Run from the AURA/ directory:
    python scripts/train_dqn.py

Outputs:
    models/dqnModel.pt

The agent learns to pick the correct ECLSS remediation action given the
current sensor state and ML pipeline outputs (IF anomaly score, RF fault
probabilities, LSTM failure probability + RUL).

Training uses oracle signals (ground-truth fault labels with added noise)
so no live ML inference is required during training — makes it fast on CPU.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import random
from collections import deque

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

from app import constants
from app.data_generator import SensorDataGenerator
from dqn_recommender import DQNNet, STATE_SIZE, N_ACTIONS

# ── Config ────────────────────────────────────────────────────────────────────
N_EPISODES    = 30_000
MAX_STEPS     = 60
BATCH_SIZE    = 256
REPLAY_SIZE   = 100_000
GAMMA         = 0.97
LR            = 5e-4
EPS_START     = 1.0
EPS_END       = 0.02
EPS_DECAY     = 18_000      # episodes to anneal over (scales with N_EPISODES)
TARGET_UPDATE = 150         # steps between target-net syncs
NOMINAL_RATIO = 0.30        # fraction of episodes with no active fault
OUTPUT_PATH   = "models/dqnModel.pt"
DEVICE        = "cpu"       # DQN is tiny; CPU is fast enough

# ── Derived constants ─────────────────────────────────────────────────────────
PARAM_ORDER      = list(constants.PARAMETER_NOMINAL_RANGES.keys())
FAULTS           = list(constants.FAULT_IMPACT_SEVERITY.keys())
ACTIONS          = constants.ACTIONS_TO_TAKE
ACTIONS_TO_FAULT = constants.ACTIONS_TO_FAULT

# Reverse: fault -> correct action index
FAULT_TO_ACTION_IDX: dict[str, int] = {}
for _action, _fault in ACTIONS_TO_FAULT.items():
    FAULT_TO_ACTION_IDX[_fault] = ACTIONS.index(_action)

print(f"Parameters : {len(PARAM_ORDER)}")
print(f"Faults     : {len(FAULTS)}")
print(f"Actions    : {len(ACTIONS)}")
print(f"State size : {STATE_SIZE}")
print(f"Fault->action map: {FAULT_TO_ACTION_IDX}")

# ── Compute scaler stats from a mixed nominal+fault dataset ──────────────────
print("\nComputing sensor normalization stats ...")
_gen_scaler = SensorDataGenerator(seed=999, step_seconds=60.0)
_raw = _gen_scaler.generate_nominal_batch(2_000)
for _f in FAULTS:
    _rows, _ = _gen_scaler.generate_fault_batch(_f, n_per_fault=100)
    _raw.extend(_rows)

X_flat       = np.array([[r.get(p, 0.0) for p in PARAM_ORDER] for r in _raw], dtype=np.float32)
SCALER_MEAN  = X_flat.mean(axis=0)
SCALER_STD   = X_flat.std(axis=0)

# ── State encoder (oracle signals with noise) ─────────────────────────────────
def encode_state(
    sensor_data: dict,
    fault_label: str | None,
    anomaly:     int,
    rul_hours:   float,
    rng:         np.random.Generator,
) -> np.ndarray:
    # Normalised sensor readings
    sensors = np.array([sensor_data.get(p, 0.0) for p in PARAM_ORDER], dtype=np.float32)
    sensors = (sensors - SCALER_MEAN) / (SCALER_STD + 1e-8)

    # Noisy oracle RF probabilities — show fault signal as soon as fault is active
    # (not just when anomaly=1) so the agent can learn the fault->action mapping.
    # This matches production behaviour where RF fires whenever IF flags anomalous.
    rf_probs = np.zeros(len(FAULTS), dtype=np.float32)
    if fault_label is not None:
        idx = FAULTS.index(fault_label)
        rf_probs[idx] = 1.0
        noise = rng.dirichlet(np.ones(len(FAULTS)) * 0.3).astype(np.float32) * 0.25
        rf_probs = np.clip(rf_probs + noise, 0.0, 1.0)
        rf_probs /= (rf_probs.sum() + 1e-8)

    # Oracle scalars (with small perturbations)
    fault_active        = float(fault_label is not None)
    anomaly_score_proxy = fault_active * float(rng.uniform(0.5, 1.0))
    if_flip = rng.random() < 0.05
    if_flag = float(1.0 - fault_active if if_flip else fault_active)
    failure_prob = float(np.clip(fault_active + float(rng.normal(0, 0.05)), 0.0, 1.0))
    rul_norm     = float(np.clip(rul_hours, 0.0, 200.0) / 200.0)

    scalars = np.array(
        [anomaly_score_proxy, if_flag, failure_prob, rul_norm], dtype=np.float32
    )
    return np.concatenate([sensors, rf_probs, scalars])


# ── Reward function ───────────────────────────────────────────────────────────
def compute_reward(
    action_idx:  int,
    fault_label: str | None,
    anomaly:     int,
) -> tuple[float, bool]:
    """Returns (reward, done).

    Reward is based on whether a fault is *active* (fault_label is not None),
    not the per-step anomaly flag.  This ensures the agent learns to act on
    the RF signal as soon as a fault starts, not only after IF detects it.
    """
    fault_active  = fault_label is not None
    action_name   = ACTIONS[action_idx]
    is_do_nothing = action_idx == 0

    if not fault_active:
        # Nominal: reward monitoring, penalise spurious interventions
        return (+1.0, False) if is_do_nothing else (-3.0, False)

    # Fault active
    if is_do_nothing:
        return -0.5, False                          # urgency penalty

    action_targets = ACTIONS_TO_FAULT.get(action_name)
    if action_targets == fault_label:
        return +20.0, True                          # correct fix -> done
    return -5.0, False                              # wrong fault targeted


# ── Episode environment ───────────────────────────────────────────────────────
class EpisodeEnv:
    # Round-robin fault index shared across all resets so every fault type
    # receives an equal number of training episodes regardless of random sampling.
    _fault_cycle_idx: int = 0

    def __init__(self, rng: np.random.Generator):
        # step_seconds=60 matches the live server — sensor drift values are
        # realistic so the DQN learns from both sensor AND rf_prob signals.
        self.gen = SensorDataGenerator(seed=int(rng.integers(0, 100_000)),
                                       step_seconds=60.0)
        self.rng = rng

    def reset(self) -> tuple[np.ndarray, str | None]:
        self.step_idx = 0
        if self.rng.random() < NOMINAL_RATIO:
            self.fault    = None
            self.sequence = self.gen.generate_sequence(
                n=MAX_STEPS + 10,
                fault=None,
                location=random.choice(constants.LOCATIONS),
            )
        else:
            # Cycle through faults in order — guarantees equal episode counts
            # for all 8 fault types instead of relying on random.choice.
            self.fault = FAULTS[EpisodeEnv._fault_cycle_idx % len(FAULTS)]
            EpisodeEnv._fault_cycle_idx += 1
            n_total    = MAX_STEPS + 10
            self.sequence = self.gen.generate_sequence(
                n=n_total,
                fault=self.fault,
                fault_start=n_total // 4,           # fault starts after 25% nominal steps
                location=random.choice(constants.LOCATIONS),
            )
        return self._state(), self.fault

    def step(self, action_idx: int) -> tuple[np.ndarray, float, bool]:
        rec     = self.sequence[self.step_idx]
        anomaly = rec["anomaly"]
        rul     = rec["rul_hours"] if rec["rul_hours"] is not None else 200.0

        reward, done = compute_reward(action_idx, self.fault, anomaly)
        if anomaly and not done:
            reward -= 0.1                           # per-step urgency cost

        self.step_idx += 1
        timed_out = self.step_idx >= len(self.sequence)
        if timed_out:
            done = True

        next_state = (
            self._state() if not done
            else np.zeros(STATE_SIZE, dtype=np.float32)
        )
        return next_state, reward, done

    def _state(self) -> np.ndarray:
        rec = self.sequence[self.step_idx]
        rul = rec["rul_hours"] if rec["rul_hours"] is not None else 200.0
        return encode_state(rec["data"], self.fault, rec["anomaly"], rul, self.rng)


# ── Build networks ────────────────────────────────────────────────────────────
policy_net = DQNNet(STATE_SIZE, N_ACTIONS).to(DEVICE)
target_net = DQNNet(STATE_SIZE, N_ACTIONS).to(DEVICE)
target_net.load_state_dict(policy_net.state_dict())
target_net.eval()

optimizer = optim.AdamW(policy_net.parameters(), lr=LR, weight_decay=1e-4)
replay    = deque(maxlen=REPLAY_SIZE)
rng       = np.random.default_rng(42)
env       = EpisodeEnv(rng)

# ── Training loop ─────────────────────────────────────────────────────────────
print(f"\nTraining for {N_EPISODES} episodes ...")
total_steps    = 0
eps            = EPS_START
ep_rewards     = []
ep_solved      = []         # did the agent pick the correct action this episode?

for ep in range(1, N_EPISODES + 1):
    state, fault = env.reset()
    ep_reward = 0.0
    solved    = False

    for _ in range(MAX_STEPS):
        # Epsilon-greedy
        if rng.random() < eps:
            action = int(rng.integers(0, N_ACTIONS))
        else:
            with torch.no_grad():
                q = policy_net(torch.tensor(state).unsqueeze(0).to(DEVICE))
            action = int(q.argmax().item())

        next_state, reward, done = env.step(action)
        if reward >= 20.0:
            solved = True

        replay.append((state, action, reward, next_state, float(done)))
        ep_reward += reward
        state = next_state
        total_steps += 1

        # Mini-batch update
        if len(replay) >= BATCH_SIZE:
            idxs  = rng.integers(0, len(replay), BATCH_SIZE)
            batch = [replay[i] for i in idxs]
            s, a, r, ns, d = zip(*batch)

            S  = torch.tensor(np.array(s),  dtype=torch.float32).to(DEVICE)
            A  = torch.tensor(a,            dtype=torch.long).to(DEVICE)
            R  = torch.tensor(r,            dtype=torch.float32).to(DEVICE)
            NS = torch.tensor(np.array(ns), dtype=torch.float32).to(DEVICE)
            D  = torch.tensor(d,            dtype=torch.float32).to(DEVICE)

            # Double DQN target
            with torch.no_grad():
                best_a   = policy_net(NS).argmax(1)
                tgt_q    = target_net(NS).gather(1, best_a.unsqueeze(1)).squeeze(1)
                tgt_val  = R + GAMMA * tgt_q * (1 - D)

            curr_q = policy_net(S).gather(1, A.unsqueeze(1)).squeeze(1)
            loss   = nn.MSELoss()(curr_q, tgt_val)

            optimizer.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(policy_net.parameters(), 1.0)
            optimizer.step()

        if total_steps % TARGET_UPDATE == 0:
            target_net.load_state_dict(policy_net.state_dict())

        if done:
            break

    # Anneal epsilon
    eps = EPS_END + (EPS_START - EPS_END) * max(0.0, 1.0 - ep / EPS_DECAY)
    ep_rewards.append(ep_reward)
    ep_solved.append(float(solved))

    if ep % 300 == 0 or ep == 1:
        avg_r = np.mean(ep_rewards[-300:])
        solve_rate = np.mean(ep_solved[-300:]) * 100
        print(f"  Episode {ep:5d}/{N_EPISODES} | "
              f"eps={eps:.3f} | avg_reward={avg_r:+.1f} | "
              f"solve_rate={solve_rate:.1f}%")

# ── Save ──────────────────────────────────────────────────────────────────────
torch.save({
    "model_state": policy_net.state_dict(),
    "state_size":  STATE_SIZE,
    "action_size": N_ACTIONS,
    "scaler_mean": SCALER_MEAN.tolist(),
    "scaler_std":  SCALER_STD.tolist(),
}, OUTPUT_PATH)
print(f"\nSaved -> {OUTPUT_PATH}")
print("Done.")
