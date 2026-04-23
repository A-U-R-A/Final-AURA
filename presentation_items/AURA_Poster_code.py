"""
AURA — Automated User Resource Analyzer
48" × 36"  (3456 × 2592 pt)
v4: every coordinate pre-calculated, AURA centered with logo,
    proper section spacing, "DASHBOARD" label, real QR code
"""

from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, Color

# ── Page ─────────────────────────────────────────────────────────────
W = 48 * 72   # 3456
H = 36 * 72   # 2592

# ── Palette ───────────────────────────────────────────────────────────
BG         = HexColor("#080808")
SURFACE    = HexColor("#101010")
SURFACE2   = HexColor("#161616")
SURFACE3   = HexColor("#1c1c1c")
BORDER     = HexColor("#242424")
BORDER2    = HexColor("#303030")
GOLD       = HexColor("#aba372")
GOLD_DIM   = HexColor("#3d3826")
GOLD_MID   = HexColor("#4d4933")
GOLD_BRT   = HexColor("#d4c99a")
GOLD_LITE  = HexColor("#c9bc8a")
WARNING    = HexColor("#ffab00")
DANGER     = HexColor("#ff3d40")
TEXT       = HexColor("#ddd8cc")
TEXT_DIM   = HexColor("#8c8470")
TEXT_BRT   = HexColor("#ffffff")
MUTED      = HexColor("#5a5045")
BLUE       = HexColor("#4a90d9")
BLUE_DIM   = HexColor("#0e1e30")
GREEN      = HexColor("#4abf6a")
GREEN_DIM  = HexColor("#0e2818")
PURPLE     = HexColor("#a855e0")
PURPLE_DIM = HexColor("#1e0e30")
AMBER_DIM  = HexColor("#2a1e04")

LOGO_PATH  = "/home/claude/Final-AURA/Final-AURA-main/images/logo_gold.png"
QR_PATH    = "/home/claude/qr_aura.png"
SCREEN_PATH = "/mnt/user-data/uploads/1776451970378_image.png"
OUT        = "/mnt/user-data/outputs/AURA_Poster.pdf"

# ── Layout grid ───────────────────────────────────────────────────────
HEADER_H = 444
FOOTER_H = 200
MARGIN   = 48
GAP      = 30
COL_W    = (W - 2*MARGIN - 2*GAP) // 3   # 1100 pt  ≈ 15.3"
CTOP     = H - HEADER_H - 40             # 2108
CBOT     = FOOTER_H + 16                  # 216
CX       = [MARGIN,
            MARGIN + COL_W + GAP,
            MARGIN + 2*(COL_W + GAP)]

# Pre-computed section overhead (rule + padding below) — keep consistent
SEC_H    = 48    # height consumed by each sec_hdr call
SGAP     = 40    # vertical gap between content blocks


# ═════════════════════════════════════════════════════════════════════
# PRIMITIVES
# ═════════════════════════════════════════════════════════════════════

def rrect(c, x, y, w, h, r=12, fill=None, stroke=None, sw=1.5):
    kw = {'fill': 1 if fill else 0, 'stroke': 1 if stroke else 0}
    if fill:   c.setFillColor(fill)
    if stroke: c.setStrokeColor(stroke); c.setLineWidth(sw)
    c.roundRect(x, y, w, h, r, **kw)


def sec_hdr(c, x, y, w, label, color=GOLD, fs=34):
    """
    Draw a section header at baseline y.
    Accent bar is vertically centred with the cap-height of the text.
    Returns y - SEC_H  (= y - 48) — the y to start content below.
    """
    cap_h   = int(fs * 0.72)          # Helvetica cap height ≈ 72 % of em
    pad     = 6                        # padding above/below cap
    bar_bot = y - pad                  # slightly below baseline
    bar_h   = cap_h + pad * 2         # cap + padding on each side
    # left accent bar — centred with the text cap height
    c.setFillColor(color)
    c.rect(x, bar_bot, 7, bar_h, fill=1, stroke=0)
    # label
    c.setFillColor(TEXT_BRT)
    c.setFont("Helvetica-Bold", fs)
    c.drawString(x + 20, y, label)
    # underrule
    c.setStrokeColor(BORDER2)
    c.setLineWidth(1)
    c.line(x, y - 12, x + w, y - 12)
    return y - SEC_H   # caller writes content starting here


def fit_lines(c, text, max_w, font, size):
    """Word-wrap text into lines that fit max_w. Returns list[str]."""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if c.stringWidth(test, font, size) > max_w:
            if cur:
                lines.append(cur)
            cur = w
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


def draw_para(c, text, x, y, max_w, font, size, color, leading=None):
    """Draw wrapped paragraph. Returns y after last line."""
    if leading is None:
        leading = round(size * 1.5)
    c.setFont(font, size)
    c.setFillColor(color)
    for ln in fit_lines(c, text, max_w, font, size):
        c.drawString(x, y, ln)
        y -= leading
    return y


def pill(c, x, y_center, text, bg=GOLD_MID, fg=GOLD_LITE, fs=17):
    """Draw pill, return x after pill (for chaining)."""
    tw = c.stringWidth(text, "Helvetica-Bold", fs)
    pw, ph = tw + 22, fs + 12
    c.setFillColor(bg)
    c.roundRect(x, y_center - ph // 2, pw, ph, ph // 2, fill=1, stroke=0)
    c.setFillColor(fg)
    c.setFont("Helvetica-Bold", fs)
    c.drawString(x + 11, y_center - fs // 2 + 2, text)
    return x + pw + 8


def stat_box(c, x, y, w, h, value, label, color=GOLD):
    rrect(c, x, y, w, h, r=12, fill=SURFACE3, stroke=color, sw=2)
    c.setFillColor(color)
    c.setFont("Helvetica-Bold", 58)
    c.drawCentredString(x + w / 2, y + h - 72, value)
    parts = label.split()
    c.setFillColor(TEXT_DIM)
    c.setFont("Helvetica", 20)
    if len(parts) == 1:
        c.drawCentredString(x + w / 2, y + 16, label)
    else:
        mid = (len(parts) + 1) // 2
        c.drawCentredString(x + w / 2, y + 30, " ".join(parts[:mid]))
        c.drawCentredString(x + w / 2, y + 8,  " ".join(parts[mid:]))


def model_card(c, x, y, w, h, title, subtitle, accent, points):
    """ML model info card. All text measured to prevent overflow."""
    rrect(c, x, y, w, h, r=12, fill=SURFACE2, stroke=accent, sw=2)
    # subtle colour wash
    c.setFillColor(Color(accent.red, accent.green, accent.blue, 0.04))
    c.roundRect(x + 2, y + 2, w - 4, h - 4, 10, fill=1, stroke=0)
    # top accent stripe
    c.setFillColor(accent)
    c.roundRect(x + 2, y + h - 10, w - 4, 10, 8, fill=1, stroke=0)

    # Title  (34 pt)
    ty = y + h - 48
    c.setFillColor(TEXT_BRT)
    c.setFont("Helvetica-Bold", 34)
    c.drawString(x + 18, ty, title)

    # Subtitle  (24 pt italic)
    ty -= 38
    c.setFillColor(accent)
    c.setFont("Helvetica-Oblique", 24)
    c.drawString(x + 18, ty, subtitle)

    # Rule
    ty -= 18
    c.setStrokeColor(BORDER2)
    c.setLineWidth(1)
    c.line(x + 18, ty, x + w - 18, ty)
    ty -= 30

    # Bullets — fixed metrics to guarantee no overflow
    BSIZE   = 24
    LEADING = 34
    EXTRA   = 10    # gap between bullets
    INDENT  = 42
    MAX_W   = w - INDENT - 20

    for pt in points:
        c.setFillColor(accent)
        c.circle(x + 26, ty + 7, 5, fill=1, stroke=0)
        lines = fit_lines(c, pt, MAX_W, "Helvetica", BSIZE)
        c.setFont("Helvetica", BSIZE)
        c.setFillColor(TEXT)
        for ln in lines:
            c.drawString(x + INDENT, ty, ln)
            ty -= LEADING
        ty -= EXTRA


def feature_card(c, x, y_top, w, h, title, detail, accent):
    """Feature card: top = y_top, extends downward h points."""
    rrect(c, x, y_top - h, w, h, r=9, fill=SURFACE2, stroke=accent, sw=2)
    c.setFillColor(accent)
    c.roundRect(x, y_top - h, 7, h, 4, fill=1, stroke=0)
    # Title
    c.setFillColor(TEXT_BRT)
    c.setFont("Helvetica-Bold", 27)
    c.drawString(x + 20, y_top - 34, title)
    # Detail — max 2 lines
    c.setFont("Helvetica", 23)
    c.setFillColor(TEXT_DIM)
    lines = fit_lines(c, detail, w - 36, "Helvetica", 23)
    dy = y_top - 62
    for ln in lines[:2]:
        c.drawString(x + 20, dy, ln)
        dy -= 30


# ═════════════════════════════════════════════════════════════════════
def make_poster():
    c = canvas.Canvas(OUT, pagesize=(W, H))
    c.setTitle("AURA — Automated User Resource Analyzer")

    # ── BACKGROUND ────────────────────────────────────────────────
    c.setFillColor(BG)
    c.rect(0, 0, W, H, fill=1, stroke=0)

    # Dot-grid texture
    c.setFillColor(HexColor("#141414"))
    for gx in range(80, W, 80):
        for gy in range(80, H, 80):
            c.circle(gx, gy, 1.2, fill=1, stroke=0)

    # Gold chrome top strip
    c.setFillColor(GOLD)
    c.rect(0, H - 5, W, 5, fill=1, stroke=0)

    # ══════════════════════════════════════════════════════════════
    # HEADER
    # ══════════════════════════════════════════════════════════════
    c.setFillColor(SURFACE)
    c.rect(0, H - HEADER_H, W, HEADER_H - 5, fill=1, stroke=0)

    # ── Logo (240 × 240, vertically centered in header) ───────────
    LOGO_SIZE  = 240
    logo_x     = 52
    logo_y     = H - HEADER_H + (HEADER_H - LOGO_SIZE) // 2   # = H - 330
    logo_cy    = logo_y + LOGO_SIZE // 2                        # = H - 210
    try:
        c.drawImage(LOGO_PATH, logo_x, logo_y, LOGO_SIZE, LOGO_SIZE, mask='auto')
    except Exception:
        pass

    # ── "AURA" wordmark — cap-height centre aligned to logo centre ─
    AURA_FS   = 160
    CAP_H     = int(AURA_FS * 0.72)          # ≈ 115 pt
    aura_base = logo_cy - CAP_H // 2         # baseline so cap centre == logo_cy
    c.setFillColor(GOLD_BRT)
    c.setFont("Helvetica-Bold", AURA_FS)
    c.drawString(316, aura_base, "AURA")

    # ── Vertical divider ──────────────────────────────────────────
    c.setFillColor(GOLD_MID)
    c.rect(804, H - HEADER_H + 44, 3, HEADER_H - 80, fill=1, stroke=0)

    # ── Subtitle block (x = 834) — centred vertically in header ──
    # Block items and their font sizes / leading:
    #   [0] "Automated User Resource Analyzer"      fs=33
    #   [1] "AI-Powered Predictive Maintenance …"   fs=26, two lines
    #   [2] rule at logo_cy (visual mid-point)
    #   [3] tagline italic                          fs=21
    #   [4] pills row                               pill_h=27
    # Total block height ≈ 36+8+26+26+8+1+8+21+14+27 = 175 pt
    # Place top of block at logo_cy + 175/2 = logo_cy + 87
    SX       = 834
    SUB_W    = W - SX - 52    # max width before right edge (clipped by stat boxes)
    PILL_MAX = 1940            # pills stop before stat boxes start (≈2040)

    blk_top  = logo_cy + 87   # top baseline of first subtitle line

    # Title
    c.setFillColor(GOLD_LITE)
    c.setFont("Helvetica-Bold", 33)
    c.drawString(SX, blk_top, "Automated User Resource Analyzer")

    # Detail lines
    c.setFillColor(TEXT_DIM)
    c.setFont("Helvetica", 26)
    c.drawString(SX, blk_top - 44, "AI-Powered Predictive Maintenance for the ISS Environmental")
    c.drawString(SX, blk_top - 76, "Control & Life Support System  (ECLSS)")

    # Rule exactly at logo_cy (visual centre of header)
    c.setStrokeColor(BORDER2)
    c.setLineWidth(1)
    c.line(SX, logo_cy, W - 52, logo_cy)

    # Tagline
    c.setFillColor(TEXT_DIM)
    c.setFont("Helvetica-Oblique", 21)
    c.drawString(SX, logo_cy - 22,
        "4 stacked ML models  ·  Real-time anomaly detection  ·  Fault classification  ·  AI remediation")

    # Tech pills row (capped so they don't run into stat boxes)
    px = SX
    py = logo_cy - 64
    for lbl in ["Python 3.11","FastAPI","PyTorch","scikit-learn","SQLite",
                "WebSocket","Isolation Forest","Random Forest","LSTM","DQN"]:
        if px > PILL_MAX:
            break
        px = pill(c, px, py, lbl, fs=17)

    # ── Stat boxes (right side of header) ────────────────────────
    stats = [
        ("<1s", "Tick Interval"),
        ("11",  "Remediation Actions"),
        ("8",   "Fault Types"),
        ("20+", "Sensor Parameters"),
        ("7",   "ISS Locations"),
        ("4",   "ML Models"),
    ]
    SB_W, SB_H = 210, 140
    sx0 = W - MARGIN - len(stats) * (SB_W + 10) + 10
    sb_y = int((logo_cy + (H - HEADER_H)) / 2 - SB_H / 2)   # centred between centre rule and bottom line
    for i, (val, lbl) in enumerate(stats):
        stat_box(c, sx0 + i * (SB_W + 10), sb_y, SB_W, SB_H, val, lbl)

    # Header bottom rule
    c.setStrokeColor(GOLD_MID)
    c.setLineWidth(1.5)
    c.line(0, H - HEADER_H, W, H - HEADER_H)

    # ══════════════════════════════════════════════════════════════
    # Column rules
    # ══════════════════════════════════════════════════════════════
    for cx_div in [CX[1] - GAP // 2, CX[2] - GAP // 2]:
        c.setStrokeColor(BORDER)
        c.setLineWidth(1)
        c.line(cx_div, CBOT - 4, cx_div, CTOP)

    # ══════════════════════════════════════════════════════════════
    # COLUMN 1 — Overview · Pipeline · Dashboard screenshot · Faults
    # ══════════════════════════════════════════════════════════════
    X1 = CX[0]
    Y1 = CTOP

    # ── WHAT IS AURA? ─────────────────────────────────────────────
    Y1 = sec_hdr(c, X1, Y1, COL_W, "WHAT IS AURA?")
    Y1 = draw_para(c,
        "AURA is a full-stack real-time dashboard that monitors the ISS Environmental "
        "Control and Life Support System across 7 station modules. It generates physics-based "
        "sensor data, runs 4 stacked ML models on every tick, and streams results live via "
        "WebSocket — giving crew and ground control early warning of faults before they "
        "become mission-critical failures.",
        X1, Y1, COL_W, "Helvetica", 28, TEXT, leading=42)
    Y1 -= SGAP

    # ── ML INFERENCE PIPELINE ─────────────────────────────────────
    Y1 = sec_hdr(c, X1, Y1, COL_W, "ML INFERENCE PIPELINE")

    steps = [
        ("1", "SENSOR\nSIM",       "Physics-based ISS\ncorrelated data",    GOLD_MID,   GOLD),
        ("2", "ISOLATION\nFOREST", "Anomaly detection\nunsupervised",        BLUE_DIM,   BLUE),
        ("3", "RANDOM\nFOREST",   "8-class fault\nidentification",          GREEN_DIM,  GREEN),
        ("4", "LSTM\nNETWORK",    "Failure prob &\nRUL prediction",         PURPLE_DIM, PURPLE),
        ("5", "DEEP\nQ-NET",      "Optimal remediation\naction selection",   AMBER_DIM,  WARNING),
    ]
    STEP_H = 180
    step_w = (COL_W - 4) // len(steps) - 8
    for i, (num, title, desc, bg, fg) in enumerate(steps):
        bx = X1 + i * (step_w + 8)
        rrect(c, bx, Y1 - STEP_H, step_w, STEP_H, r=10, fill=bg, stroke=fg, sw=2)
        # number badge
        c.setFillColor(fg)
        c.circle(bx + step_w / 2, Y1 - 26, 17, fill=1, stroke=0)
        c.setFillColor(BG)
        c.setFont("Helvetica-Bold", 17)
        c.drawCentredString(bx + step_w / 2, Y1 - 31, num)
        # title lines
        c.setFillColor(fg)
        c.setFont("Helvetica-Bold", 19)
        ty = Y1 - 68
        for ln in title.split("\n"):
            c.drawCentredString(bx + step_w / 2, ty, ln)
            ty -= 22
        # description lines
        c.setFillColor(TEXT_DIM)
        c.setFont("Helvetica", 17)
        ty -= 4
        for ln in desc.split("\n"):
            c.drawCentredString(bx + step_w / 2, ty, ln)
            ty -= 20
        # arrow between boxes
        if i < len(steps) - 1:
            ax = bx + step_w + 2
            ay = Y1 - STEP_H // 2
            c.setFillColor(GOLD_MID)
            path = c.beginPath()
            path.moveTo(ax, ay - 5)
            path.lineTo(ax, ay + 5)
            path.lineTo(ax + 6, ay)
            path.close()
            c.drawPath(path, fill=1, stroke=0)
    Y1 -= STEP_H + SGAP

    # ── DASHBOARD ─────────────────────────────────────────────────
    Y1 = sec_hdr(c, X1, Y1, COL_W, "DASHBOARD")

    IMG_H = 570
    rrect(c, X1, Y1 - IMG_H, COL_W, IMG_H, r=12, fill=SURFACE2, stroke=GOLD_MID, sw=2)
    # Draw the real screenshot, letterboxed to fit
    img_aspect = 1104 / 583
    box_aspect = COL_W / IMG_H
    if img_aspect > box_aspect:
        # image wider than box — fit width
        draw_w = COL_W - 8
        draw_h = draw_w / img_aspect
        draw_x = X1 + 4
        draw_y = Y1 - IMG_H + (IMG_H - draw_h) / 2
    else:
        # image taller — fit height
        draw_h = IMG_H - 8
        draw_w = draw_h * img_aspect
        draw_x = X1 + (COL_W - draw_w) / 2
        draw_y = Y1 - IMG_H + 4
    try:
        c.drawImage(SCREEN_PATH, draw_x, draw_y, draw_w, draw_h,
                    mask='auto', preserveAspectRatio=True)
    except Exception as e:
        print(f"Screenshot error: {e}")
    # gold caption bar at bottom of image box
    c.setFillColor(Color(0, 0, 0, 0.55))
    c.roundRect(X1 + 4, Y1 - IMG_H + 4, COL_W - 8, 36, 8, fill=1, stroke=0)
    c.setFillColor(GOLD_LITE)
    c.setFont("Helvetica-Bold", 19)
    c.drawString(X1 + 18, Y1 - IMG_H + 14, "AURA  ·  ISS 3D Digital Twin  ·  Live fault status overlay")
    Y1 -= IMG_H + SGAP

    # ── FAULT TYPES DETECTED ──────────────────────────────────────
    Y1 = sec_hdr(c, X1, Y1, COL_W, "FAULT TYPES DETECTED")

    faults = [
        (DANGER,              "Cabin Leak",                   "Cabin pressure / N2 drop — structural seal breach"),
        (BLUE,                "O2 Generator Failure",         "OGS electrolysis cell stack degradation"),
        (HexColor("#5ab4ff"), "O2 Leak",                      "O2 plumbing fitting / valve seal failure"),
        (WARNING,             "CO2 Scrubber Failure",         "CDRA zeolite sorbent bed exhausted"),
        (GREEN,               "CHX Failure",                  "CCAA heat exchanger hydrophilic coating loss"),
        (HexColor("#6ab4d4"), "Water Processor Failure",      "WPA sieve / MF bed contamination"),
        (HexColor("#d4a43a"), "Trace Contaminant Saturation", "TCCS charcoal bed — NH3 / CO breakthrough"),
        (HexColor("#e05555"), "NH3 Coolant Leak",             "EATCS toxic ammonia loop breach"),
    ]
    # Divide remaining space evenly
    avail_f = Y1 - CBOT
    f_h     = avail_f // len(faults) - 6

    for col2, name, detail in faults:
        rrect(c, X1, Y1 - f_h, COL_W, f_h, r=8, fill=SURFACE2, stroke=BORDER2, sw=1)
        # colour left edge
        c.setFillColor(col2)
        c.roundRect(X1, Y1 - f_h, 7, f_h, 4, fill=1, stroke=0)
        # fault name — centred vertically in top half of card
        name_y = Y1 - f_h + f_h - 26
        c.setFillColor(TEXT_BRT)
        c.setFont("Helvetica-Bold", 24)
        c.drawString(X1 + 20, name_y, name)
        # detail — centred in bottom half
        c.setFillColor(TEXT_DIM)
        c.setFont("Helvetica", 20)
        c.drawString(X1 + 20, Y1 - f_h + 10, detail)
        Y1 -= f_h + 6

    # ══════════════════════════════════════════════════════════════
    # COLUMN 2 — 4 ML Model Cards + screenshot placeholder
    # ══════════════════════════════════════════════════════════════
    X2 = CX[1]
    Y2 = CTOP

    Y2 = sec_hdr(c, X2, Y2, COL_W, "MACHINE LEARNING STACK")

    # Divide remaining height: 4 cards + 14 pt gaps + photo placeholder
    avail2  = Y2 - CBOT
    PHOTO_H = 200
    # 4*(card_h + 14) + PHOTO_H = avail2
    card_h  = (avail2 - PHOTO_H - 4 * 14) // 4

    model_defs = [
        {
            "title":   "Isolation Forest",
            "sub":     "Anomaly Detection  ·  Layer 1",
            "accent":  BLUE,
            "pts": [
                "Unsupervised — trained on nominal ISS data only; no labeled faults required",
                "Isolates anomalies by random partitioning; shorter path = stronger outlier",
                "5-consecutive-tick filter: P(5 FPs at 3% FPR) ≈ 2 × 10⁻⁶ per day",
                "Per-location 300 s cooldown prevents repeat alert fatigue",
                "IF label passed as binary feature into every downstream model layer",
            ],
        },
        {
            "title":   "Random Forest",
            "sub":     "Fault Classification  ·  Layer 2",
            "accent":  GREEN,
            "pts": [
                "Multi-class classifier: predicts which of 8 fault types is currently active",
                "Outputs a full probability distribution across all fault classes each tick",
                "≥ 60 % confidence activates the live fault indicator in the UI",
                "≥ 90 % confidence latches fault display until crew manually resolves it",
                "Trained on physics-derived sensor drift signatures per fault type",
            ],
        },
        {
            "title":   "LSTM Neural Network",
            "sub":     "Failure Prediction  ·  Layer 3",
            "accent":  PURPLE,
            "pts": [
                "Long Short-Term Memory captures temporal degradation in sensor sequences",
                "Outputs failure probability [0 – 1] and Remaining Useful Life in hours",
                "Per-location rolling buffer resets on fault injection for a clean signal",
                "Enables proactive maintenance hours before fault reaches critical state",
                "failure_prob and RUL forwarded as state inputs to the DQN layer below",
            ],
        },
        {
            "title":   "Deep Q-Network (DQN)",
            "sub":     "Remediation Recommendation  ·  Layer 4",
            "accent":  WARNING,
            "pts": [
                "Reinforcement learning: maps full system state → optimal repair action",
                "11 discrete actions: sealant, O2 valve isolation, EVA prep, bed R&R, …",
                "State vector: sensor readings, anomaly score, RF class, LSTM probability",
                "Outputs action index + confidence from Q-value softmax distribution",
                "Recommendations displayed live per ISS module in the dashboard UI",
            ],
        },
    ]

    for m in model_defs:
        model_card(c, X2, Y2 - card_h, COL_W, card_h,
                   m["title"], m["sub"], m["accent"], m["pts"])
        Y2 -= card_h + 14

    # Screenshot — maintenance cards
    ph_h = Y2 - CBOT - 4
    TREND_PATH = "/mnt/user-data/uploads/1776453439910_image.png"
    if ph_h > 40:
        rrect(c, X2, CBOT + 2, COL_W, ph_h, r=12, fill=SURFACE2, stroke=GOLD_MID, sw=2)
        img_w_src, img_h_src = 505, 101
        draw_w = COL_W - 16
        draw_h = draw_w * img_h_src / img_w_src
        if draw_h > ph_h - 16:
            draw_h = ph_h - 16
            draw_w = draw_h * img_w_src / img_h_src
        draw_x = X2 + (COL_W - draw_w) / 2
        draw_y = CBOT + 2 + (ph_h - draw_h) / 2
        try:
            c.drawImage(TREND_PATH, draw_x, draw_y, draw_w, draw_h, mask='auto')
        except Exception as e:
            print(f"Maintenance img error: {e}")

    # ══════════════════════════════════════════════════════════════
    # COLUMN 3 — Key Features · Alert Design · DB Schema · Photo
    # ══════════════════════════════════════════════════════════════
    X3 = CX[2]
    Y3 = CTOP

    # ── KEY FEATURES ──────────────────────────────────────────────
    Y3 = sec_hdr(c, X3, Y3, COL_W, "KEY FEATURES")

    feats = [
        (BLUE,                "Real-Time WebSocket Streaming",
         "Sub-second sensor ticks broadcast to all connected browsers via FastAPI WebSocket."),
        (GREEN,               "Fault Latch Mechanism",
         "At 90 % RF confidence the fault is pinned per-location until crew explicitly resolves it."),
        (PURPLE,              "LSTM + DQN Integration",
         "Failure probability and RUL from LSTM feed directly into the DQN state vector."),
        (DANGER,              "Smart Alert Debouncing",
         "5-tick threshold + 5 min cooldown: P(false sustained alarm at 3 % FPR) ≈ 2×10⁻⁶/day."),
        (WARNING,             "AI Narrative Analyst",
         "Ollama / Mistral LLM generates natural-language diagnostics per location on demand."),
        (GOLD,                "Trend Detection",
         "Sen's slope + CUSUM detects gradual parameter drift before the anomaly threshold."),
        (HexColor("#6ab4d4"), "Maintenance Scheduler",
         "MTBF-based replacement and drift-based calibration schedules for all 8 subsystems."),
        (HexColor("#d4a43a"), "Digital Twin Floorplan",
         "Live SVG floorplan of the ISS overlaid with per-location fault states and severity."),
    ]
    FEAT_H = 104
    for ac, title, detail in feats:
        feature_card(c, X3, Y3, COL_W, FEAT_H, title, detail, ac)
        Y3 -= FEAT_H + 8
    Y3 -= SGAP

    # ── ALERT SYSTEM DESIGN ───────────────────────────────────────
    Y3 = sec_hdr(c, X3, Y3, COL_W, "ALERT SYSTEM DESIGN")

    alert_rows = [
        ("IF flags anomalous reading",         "1 TICK",   BLUE),
        ("N = 5 consecutive anomalous ticks",  "TRIGGER",  GOLD),
        ("RF classifies fault at ≥ 60 %",      "LIVE UI",  GREEN),
        ("RF confidence reaches ≥ 90 %",       "LATCH",    WARNING),
        ("Alert fired — 5 min cooldown reset", "CRITICAL", DANGER),
    ]
    AR_H = 58
    for label, badge_txt, ac in alert_rows:
        rrect(c, X3, Y3 - AR_H, COL_W, AR_H, r=8, fill=SURFACE3, stroke=BORDER2, sw=1)
        # dot
        c.setFillColor(ac)
        c.circle(X3 + 22, Y3 - AR_H / 2, 9, fill=1, stroke=0)
        # label — vertically centred
        c.setFillColor(TEXT)
        c.setFont("Helvetica", 24)
        c.drawString(X3 + 44, Y3 - AR_H / 2 - 9, label)
        # badge on right
        bw = c.stringWidth(badge_txt, "Helvetica-Bold", 18) + 24
        rrect(c, X3 + COL_W - bw - 12, Y3 - AR_H + 10, bw, 38, r=6, fill=ac)
        c.setFillColor(BG)
        c.setFont("Helvetica-Bold", 18)
        c.drawCentredString(X3 + COL_W - bw / 2 - 12, Y3 - AR_H + 22, badge_txt)
        Y3 -= AR_H + 6
    Y3 -= SGAP

    # ── DATABASE SCHEMA ───────────────────────────────────────────
    Y3 = sec_hdr(c, X3, Y3, COL_W, "DATABASE SCHEMA  (SQLite)")

    tables = [
        ("sensor_readings",  ["id  INTEGER PK", "location  TEXT", "timestamp  TEXT", "data  JSON"], GOLD),
        ("sensor_labels",    ["reading_id  FK",  "if_label  INT",  "rf_classification  JSON"],        BLUE),
        ("alerts",           ["id  PK",          "location / severity", "fault_type", "acknowledged"],DANGER),
        ("active_faults",    ["location  PK",    "fault_name  TEXT", "start_time  TEXT"],             GREEN),
    ]
    TBL_W = (COL_W - 12) // 2 - 4
    TBL_H = 156
    for i, (tname, cols, fg) in enumerate(tables):
        tx2 = CX[2]     if (i % 2 == 0) else CX[2] + TBL_W + 14
        ty2 = Y3        if (i < 2)      else Y3 - (TBL_H + 12)
        HDR_H = 34
        ROW_H = (TBL_H - HDR_H) // len(cols)
        rrect(c, tx2, ty2 - TBL_H, TBL_W, TBL_H, r=8, fill=SURFACE2, stroke=fg, sw=2)
        c.setFillColor(fg)
        c.roundRect(tx2 + 2, ty2 - HDR_H + 2, TBL_W - 4, HDR_H - 4, 6, fill=1, stroke=0)
        c.setFillColor(BG)
        c.setFont("Helvetica-Bold", 18)
        c.drawCentredString(tx2 + TBL_W / 2, ty2 - HDR_H + 10, tname)
        for j, col_name in enumerate(cols):
            ry = ty2 - HDR_H - j * ROW_H
            if j % 2 == 0:
                c.setFillColor(HexColor("#0f0f0f"))
                c.rect(tx2 + 2, ry - ROW_H, TBL_W - 4, ROW_H, fill=1, stroke=0)
            c.setFillColor(TEXT_DIM)
            c.setFont("Helvetica", 17)
            c.drawString(tx2 + 10, ry - ROW_H + 6, col_name)
    Y3 -= 2 * (TBL_H + 12) + SGAP

    # ── Alert screenshot ──────────────────────────────────────────
    ph3_h = Y3 - CBOT - 4
    ALERT_PATH = "/mnt/user-data/uploads/1776452729474_image.png"
    if ph3_h > 40:
        rrect(c, X3, CBOT + 2, COL_W, ph3_h, r=12, fill=SURFACE2, stroke=GOLD_MID, sw=2)
        # image: 603×56 — scale to fill width with padding
        img_w_src, img_h_src = 603, 56
        draw_w = COL_W - 28
        draw_h = draw_w * img_h_src / img_w_src
        draw_x = X3 + 14
        draw_y = CBOT + 2 + (ph3_h - draw_h) / 2   # vertically centred
        try:
            c.drawImage(ALERT_PATH, draw_x, draw_y, draw_w, draw_h, mask='auto')
        except Exception as e:
            print(f"Alert img error: {e}")

    # ══════════════════════════════════════════════════════════════
    # FOOTER
    # ══════════════════════════════════════════════════════════════
    c.setFillColor(SURFACE)
    c.rect(0, 0, W, FOOTER_H, fill=1, stroke=0)
    c.setStrokeColor(GOLD)
    c.setLineWidth(3)
    c.line(0, FOOTER_H, W, FOOTER_H)

    # Left: tech stack  (x=52, top-down from y=FOOTER_H-28)
    c.setFillColor(GOLD_LITE)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(52, FOOTER_H - 30, "TECHNOLOGY STACK")

    stack = [
        ("Backend",    "Python 3.11  ·  FastAPI 0.115  ·  Uvicorn  ·  asyncio  ·  WebSocket  ·  Pydantic"),
        ("ML / AI",    "PyTorch ≥2.0  ·  scikit-learn ≥1.5  ·  Isolation Forest  ·  Random Forest  ·  LSTM  ·  DQN"),
        ("Database",   "SQLite3  ·  JSON column storage  ·  5,000-row rolling buffer  ·  WAL journaling"),
        ("Frontend",   "Vanilla JS  ·  CSS custom properties  ·  WebSocket client  ·  SVG ISS digital twin"),
        ("AI Analyst", "Ollama (Mistral)  ·  on-demand natural-language diagnostics per location via REST"),
        ("Deploy",     "Cross-platform: Windows .bat  /  Linux .sh  ·  Zero Docker dependency"),
    ]
    sy = FOOTER_H - 58
    for label, detail in stack:
        lw = c.stringWidth(label + ":  ", "Helvetica-Bold", 20)
        c.setFillColor(GOLD_LITE)
        c.setFont("Helvetica-Bold", 20)
        c.drawString(52, sy, label + ":")
        c.setFillColor(TEXT_DIM)
        c.setFont("Helvetica", 20)
        c.drawString(52 + lw, sy, detail)
        sy -= 25

    # Centre block — vertically centred between footer top and bottom
    # Block: acronym (32pt) ── rule ── subtitle (26pt)
    GAP1    = 24          # acronym baseline → rule
    GAP2    = 32          # rule → subtitle baseline
    CAP_A   = int(32 * 0.72)   # ≈ 23  (cap height above baseline)
    DESC_S  = 7                # descender below subtitle baseline
    # Solve: (acronym_top + subtitle_bottom) / 2 = FOOTER_H / 2
    subtitle_y = (FOOTER_H - GAP1 - GAP2 - CAP_A + DESC_S) / 2
    rule_y     = subtitle_y + GAP2
    acronym_y  = rule_y + GAP1

    # -- Acronym --
    acronym = [("A", "utomated "), ("U", "ser "), ("R", "esource "), ("A", "nalyzer")]
    total_w = sum(
        c.stringWidth(lt, "Helvetica-Bold", 32) + c.stringWidth(rest, "Helvetica", 32)
        for lt, rest in acronym
    )
    ax = W / 2 - total_w / 2
    for lt, rest in acronym:
        c.setFillColor(GOLD_BRT)
        c.setFont("Helvetica-Bold", 32)
        c.drawString(ax, acronym_y, lt)
        ax += c.stringWidth(lt, "Helvetica-Bold", 32)
        c.setFillColor(TEXT_DIM)
        c.setFont("Helvetica", 32)
        c.drawString(ax, acronym_y, rest)
        ax += c.stringWidth(rest, "Helvetica", 32)

    # -- Rule --
    c.setStrokeColor(GOLD_MID)
    c.setLineWidth(1)
    c.line(W / 2 - 500, rule_y, W / 2 + 500, rule_y)

    # -- Subtitle --
    c.setFillColor(TEXT_DIM)
    c.setFont("Helvetica-Oblique", 26)
    c.drawCentredString(W / 2, subtitle_y,
        "Computer Science  ·  Database Systems  ·  Final Project 2026")

    # ── QR code card (bottom-right of footer) ────────────────────
    QR_SIZE = 152
    qr_card_w  = QR_SIZE + 32
    qr_card_h  = QR_SIZE + 48
    qr_card_x  = W - MARGIN - qr_card_w
    qr_card_y  = 12
    qr_cx      = qr_card_x + qr_card_w / 2   # true horizontal centre of card

    rrect(c, qr_card_x, qr_card_y, qr_card_w, qr_card_h,
          r=10, fill=SURFACE2, stroke=GOLD_MID, sw=2)

    # URL label — centred on card
    c.setFillColor(GOLD_LITE)
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(qr_cx, qr_card_y + qr_card_h - 20, "aurahunch.space")

    # QR image
    try:
        c.drawImage(QR_PATH, qr_card_x + 16, qr_card_y + 16,
                    QR_SIZE, QR_SIZE, mask='auto')
    except Exception as e:
        print(f"QR error: {e}")

    # Bottom chrome strip
    c.setFillColor(GOLD_MID)
    c.rect(0, 0, W, 4, fill=1, stroke=0)

    c.save()
    print(f"Saved → {OUT}")


if __name__ == "__main__":
    make_poster()
