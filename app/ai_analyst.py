import os
import time

from . import constants

# ---------------------------------------------------------------------------
# Model mapping: Ollama name → Groq equivalent
# ---------------------------------------------------------------------------
GROQ_MODEL_MAP = {
    "mistral":     "llama-3.3-70b-versatile",
    "llama3.1:8b": "llama-3.3-70b-versatile",
    "llama3":      "llama-3.3-70b-versatile",
}
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"

# Readings included in the per-turn data snapshot.
# Ollama (large context) gets more; Groq free tier is token-constrained.
OLLAMA_READINGS_PER_LOCATION = 10
GROQ_READINGS_PER_LOCATION   = 3

# ---------------------------------------------------------------------------
# Ollama availability cache (re-checked every 60 s)
# ---------------------------------------------------------------------------
_ollama_ok: bool | None = None
_ollama_checked_at: float = 0.0
_OLLAMA_CHECK_TTL = 60.0


def _is_ollama_available() -> bool:
    """Check whether a local Ollama daemon is reachable, with a 60-second result cache.
    Calling ollama.list() is a lightweight HTTP probe — if it raises, Ollama is down."""
    global _ollama_ok, _ollama_checked_at
    now = time.monotonic()
    if _ollama_ok is not None and (now - _ollama_checked_at) < _OLLAMA_CHECK_TTL:
        return _ollama_ok
    try:
        import ollama
        ollama.list()
        _ollama_ok = True
    except Exception:
        _ollama_ok = False
    _ollama_checked_at = now
    return _ollama_ok


def get_backend() -> str:
    """Return 'ollama', 'groq', or 'none' — the highest-priority available backend.
    Ollama (local, no rate limit) takes precedence over Groq (remote, free-tier limited)."""
    if _is_ollama_available():
        return "ollama"
    if os.getenv("GROQ_API_KEY"):
        return "groq"
    return "none"


# ---------------------------------------------------------------------------
# Static system prompt — role + config only, NO raw sensor data.
# Kept small so it doesn't eat into the per-turn token budget.
# ---------------------------------------------------------------------------

_STATIC_SYSTEM_PROMPT: str | None = None   # built once and cached


def _get_static_system_prompt() -> str:
    global _STATIC_SYSTEM_PROMPT
    if _STATIC_SYSTEM_PROMPT is not None:
        return _STATIC_SYSTEM_PROMPT

    nom  = _compact(constants.PARAMETER_NOMINAL_RANGES)
    units = _compact(constants.PARAMETER_UNITS)
    faults = ", ".join(constants.FAULT_IMPACT_SEVERITY.keys())
    actions = "\n".join(f"  - {a}" for a in constants.ACTIONS_TO_TAKE)

    _STATIC_SYSTEM_PROMPT = f"""You are AURA, an AI embedded in the ISS Environmental Control and Life Support System (ECLSS). You are a knowledgeable, calm, and friendly colleague — not a status report machine.

PERSONALITY:
- Be conversational and natural. If someone says hello, say hello back. If they ask how you're doing, respond like a person would.
- Do NOT proactively dump alerts, anomalies, or sensor data unless the user actually asks about them.
- When you do discuss system data, be focused and specific — answer what was asked, don't enumerate everything you know.
- You can be concise. Short answers are often better than long ones.
- You have awareness of the system state at all times, but you don't need to lead every message with it.

READ-ONLY ACCESS: You can observe all sensor data but cannot issue commands, change values, or modify alerts.

REFERENCE DATA (use when relevant to the conversation):
Nominal ranges: {nom}
Units: {units}
Known fault types: {faults}
Available actions (recommend only):
{actions}

WHEN DOING ANALYSIS:
- IF label: -1 = anomalous, 1 = normal. RF gives fault probabilities.
- Cite specific values and timestamps. Be precise — this is a life-support system.
- Keep recommendations focused on the exact action from the list above."""

    return _STATIC_SYSTEM_PROMPT


def _compact(d: dict) -> str:
    """Produce a compact single-line dict string for embedding in the system prompt.
    Shorter than json.dumps — avoids quote noise and uses less of the context window."""
    return "{" + ", ".join(f"{k}: {v}" for k, v in d.items()) + "}"


# ---------------------------------------------------------------------------
# Per-turn compact snapshot — injected into the current user message only.
# Uses pipe-separated tables instead of JSON to minimise token count.
# ---------------------------------------------------------------------------

def _build_snapshot(db, n_readings: int) -> str:
    """Build a compact tabular snapshot of current system state for injection into the AI prompt.
    Uses pipe-separated tables instead of JSON to minimise token count.
    Injected only into the LAST user message turn — previous history is left untouched
    so token cost stays O(1) per turn rather than O(n_turns)."""
    lines = ["[BACKGROUND SYSTEM DATA — reference this only if relevant to the user's message]"]

    # ── Location status table ─────────────────────────────────────────────
    lines.append("LOCATION STATUS:")
    lines.append("Location | State | Anom/Total | Top Fault (conf)")
    for loc in constants.LOCATIONS:
        latest   = db.get_latest_reading(loc)
        readings = db.get_recent_readings(loc, n=n_readings)

        if_lbl = latest.get("if_label") if latest else None
        status = "ANOMALOUS" if if_lbl == -1 else "NOMINAL" if if_lbl == 1 else "NO DATA"

        anom  = sum(1 for r in readings if r.get("if_label") == -1)
        total = len(readings)

        # Aggregate RF votes for top fault
        fault_votes: dict[str, float] = {}
        for r in readings:
            rf = r.get("rf_classification")
            if rf and r.get("if_label") == -1:
                top_f, top_p = max(rf.items(), key=lambda x: x[1])
                fault_votes[top_f] = fault_votes.get(top_f, 0) + float(top_p)
        if fault_votes:
            tf = max(fault_votes, key=fault_votes.get)
            tf_str = f"{tf} ({fault_votes[tf] / max(anom, 1):.2f})"
        else:
            tf_str = "—"

        lines.append(f"{loc} | {status} | {anom}/{total} | {tf_str}")

    # ── Recent alerts ─────────────────────────────────────────────────────
    lines.append("")
    lines.append("RECENT ALERTS (last 10):")
    alerts = db.get_alerts(limit=10)
    if alerts:
        for a in alerts:
            ack  = "ACK" if a.get("acknowledged") else "UNACK"
            ts   = str(a["timestamp"]).split(".")[0]
            line = f"[{ack}] {ts} | {a['location']} | {a['severity']}"
            if a.get("fault_type"):
                line += f" | {a['fault_type']} p={a.get('top_probability', 0):.2f}"
            lines.append(line)
    else:
        lines.append("No recent alerts.")

    # ── Sensor readings (tabular) ─────────────────────────────────────────
    lines.append("")
    lines.append(f"SENSOR READINGS (last {n_readings} per location):")

    # Determine the full ordered parameter list from the first available reading
    params: list[str] = []
    for loc in constants.LOCATIONS:
        sample = db.get_latest_reading(loc)
        if sample and sample.get("data"):
            params = list(sample["data"].keys())
            break

    if params:
        header = "ts | IF | RF_top_fault | " + " | ".join(params)
        for loc in constants.LOCATIONS:
            readings = db.get_recent_readings(loc, n=n_readings)
            if not readings:
                continue
            lines.append(f"\n{loc}:")
            lines.append(header)
            for r in readings:
                ts   = str(r.get("timestamp", "")).split(".")[0].split("T")[-1]
                ifl  = str(r.get("if_label", "?"))
                rf   = r.get("rf_classification") or {}
                rf_top = ""
                if rf:
                    tf, tp = max(rf.items(), key=lambda x: x[1])
                    rf_top = f"{tf}:{tp:.2f}"
                data = r.get("data") or {}
                vals = " | ".join(
                    f"{data.get(p, '?'):.3g}" if isinstance(data.get(p), float)
                    else str(data.get(p, "?"))
                    for p in params
                )
                lines.append(f"{ts} | {ifl} | {rf_top} | {vals}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Synchronous streaming generators
# ---------------------------------------------------------------------------

def _stream_ollama(messages: list, model: str):
    """Generator that yields text tokens from Ollama's streaming chat endpoint.
    Handles both object-style (chunk.message.content) and dict-style chunk formats
    for forward-compatibility with different Ollama client versions."""
    import ollama
    for chunk in ollama.chat(model=model, messages=messages, stream=True):
        try:
            content = chunk.message.content or ""
        except AttributeError:
            content = (chunk.get("message", {}).get("content", "")
                       if isinstance(chunk, dict) else "")
        if content:
            yield content


def _stream_groq(messages: list, model: str):
    """Generator that yields text tokens from the Groq API.
    Maps Ollama model names (mistral, llama3) to equivalent Groq model IDs via GROQ_MODEL_MAP.
    temperature=0.3 and max_tokens=2048 keep responses focused and within free-tier limits."""
    from groq import Groq
    groq_model = GROQ_MODEL_MAP.get(model, GROQ_DEFAULT_MODEL)
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    stream = client.chat.completions.create(
        model=groq_model,
        messages=messages,
        stream=True,
        max_tokens=2048,
        temperature=0.3,
    )
    for chunk in stream:
        content = chunk.choices[0].delta.content or ""
        if content:
            yield content


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def chat_stream(messages: list, model: str, db):
    """
    Build the message list for the AI backend and return (backend, generator).

    Strategy:
      - System prompt: static role + config only (~400 tokens, cached).
      - Data snapshot: compact tabular, injected into the LAST user message
        only. Previous turns in history are left untouched so token cost
        doesn't compound across the conversation.

    The generator is synchronous — the caller must run it in a thread pool.
    """
    use_ollama = _is_ollama_available()
    n_readings = OLLAMA_READINGS_PER_LOCATION if use_ollama else GROQ_READINGS_PER_LOCATION

    system_prompt = _get_static_system_prompt()
    snapshot      = _build_snapshot(db, n_readings=n_readings)

    # Inject snapshot into the last user message only
    if messages and messages[-1]["role"] == "user":
        augmented_last = {
            "role":    "user",
            "content": f"{snapshot}\n\n---\n{messages[-1]['content']}",
        }
        history      = messages[:-1]
        augmented    = history + [augmented_last]
    else:
        # Fallback: append snapshot as a standalone user message
        augmented = messages + [{"role": "user", "content": snapshot}]

    full_messages = [{"role": "system", "content": system_prompt}] + augmented

    if use_ollama:
        return "ollama", _stream_ollama(full_messages, model)

    if os.getenv("GROQ_API_KEY"):
        return "groq", _stream_groq(full_messages, model)

    def _no_backend():
        yield (
            "No AI backend is available.\n\n"
            "**Option 1 — Local Ollama:** run `ollama serve` and ensure a model is pulled "
            "(e.g. `ollama pull llama3.1:8b`).\n\n"
            "**Option 2 — Groq fallback:** set the `GROQ_API_KEY` environment variable "
            "before starting the server."
        )
    return "none", _no_backend()
