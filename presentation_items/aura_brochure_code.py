"""
AURA Trifold Accordion (Z-Fold) Brochure — WITH UI SCREENSHOTS
Letter paper landscape (11" × 8.5") | 3 panels × 2 sides
"""
import math, os
import qrcode
from PIL import Image as PILImage

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, Color

# ── Dimensions ─────────────────────────────────────────────────────────────────
PAGE_W, PAGE_H = 11 * inch, 8.5 * inch
PANEL_W = PAGE_W / 3
PANEL_H = PAGE_H
BLEED   = 0.06 * inch

# ── Palette ────────────────────────────────────────────────────────────────────
BG       = HexColor("#0e0e0e")
SURFACE  = HexColor("#161616")
SURFACE2 = HexColor("#1e1e1e")
BORDER   = HexColor("#2a2a2a")
GOLD     = HexColor("#aba372")
GOLD_DIM = HexColor("#4d4933")
GOLD_LT  = HexColor("#ccc08f")
WARNING  = HexColor("#ffab00")
DANGER   = HexColor("#ff3d40")
TEXT     = HexColor("#ddd8cc")
DIM      = HexColor("#8c8470")
MUTED    = HexColor("#6b6555")
WHITE    = HexColor("#ffffff")

# ── Helpers ────────────────────────────────────────────────────────────────────
def px(panel_index, x=0):
    return panel_index * PANEL_W + x

def fill_panel(c, panel_index, color=BG):
    c.setFillColor(color)
    c.rect(px(panel_index), 0, PANEL_W + BLEED, PANEL_H, fill=1, stroke=0)

def draw_line_h(c, panel_index, y, color=GOLD_DIM, width=0.5, margin=0.22):
    c.setStrokeColor(color)
    c.setLineWidth(width)
    c.line(px(panel_index)+margin*inch, y, px(panel_index)+PANEL_W-margin*inch, y)

def gold_bar(c, panel_index, y, h=0.04*inch):
    c.setFillColor(GOLD)
    c.rect(px(panel_index), y, PANEL_W, h, fill=1, stroke=0)

def txt(c, s, x, y, size=8, color=TEXT, font="Helvetica", align="left"):
    c.setFont(font, size)
    c.setFillColor(color)
    if align == "center":
        c.drawCentredString(x, y, s)
    elif align == "right":
        c.drawRightString(x, y, s)
    else:
        c.drawString(x, y, s)

def wrap(c, s, x, y, w, size=7.5, color=TEXT, font="Helvetica", lead=11):
    c.setFont(font, size)
    c.setFillColor(color)
    words = s.split()
    line  = ""
    cy    = y
    for word in words:
        test = (line + " " + word).strip()
        if c.stringWidth(test, font, size) <= w:
            line = test
        else:
            if line:
                c.drawString(x, cy, line)
                cy -= lead
            line = word
    if line:
        c.drawString(x, cy, line)
        cy -= lead
    return cy

def bullet(c, s, x, y, w, size=7, color=TEXT, bc=GOLD):
    bx = x + 0.13*inch
    c.setFont("Helvetica", size)
    c.setFillColor(bc)
    c.drawString(x, y, "▸")
    c.setFillColor(color)
    words = s.split()
    line  = ""
    cy    = y
    for word in words:
        test = (line + " " + word).strip()
        if c.stringWidth(test, "Helvetica", size) <= w - 0.15*inch:
            line = test
        else:
            if line:
                c.drawString(bx, cy, line)
                cy -= 10
            line = word
    if line:
        c.drawString(bx, cy, line)
        cy -= 10
    return cy - 2

def sec(c, pi, y, label, sub=""):
    y -= 5  # buffer between the rule above and the heading
    lx = px(pi) + 0.2*inch
    c.setFillColor(GOLD_DIM)
    c.rect(lx, y-1, 0.025*inch, 13, fill=1, stroke=0)
    c.setFont("Helvetica-Bold", 8.5)
    c.setFillColor(GOLD_LT)
    c.drawString(lx+0.07*inch, y+2, label.upper())
    if sub:
        c.setFont("Helvetica", 6.5)
        c.setFillColor(DIM)
        c.drawString(lx+0.07*inch, y-7, sub)
    return y - (22 if sub else 15)

def pill(c, x, y, label, bg=GOLD_DIM, fg=GOLD_LT, size=6):
    w2 = c.stringWidth(label, "Helvetica-Bold", size) + 9
    c.setFillColor(bg)
    c.roundRect(x, y-1.5, w2, 10, 2, fill=1, stroke=0)
    c.setFont("Helvetica-Bold", size)
    c.setFillColor(fg)
    c.drawCentredString(x+w2/2, y+1, label)
    return x + w2 + 4

def gradient(c, pi, y_top, h, c1, c2, steps=20):
    sh = h / steps
    for i in range(steps):
        t = i / (steps-1)
        r = c1.red   + t*(c2.red   - c1.red)
        g = c1.green + t*(c2.green - c1.green)
        b = c1.blue  + t*(c2.blue  - c1.blue)
        c.setFillColor(Color(r, g, b))
        c.rect(px(pi), y_top-(i+1)*sh, PANEL_W+BLEED, sh+0.5, fill=1, stroke=0)

def stars(c, pi, seed=1, n=55):
    import random
    rng = random.Random(seed)
    for _ in range(n):
        sx = px(pi) + rng.uniform(0, PANEL_W)
        sy = rng.uniform(0.05*inch, PANEL_H-0.05*inch)
        r  = rng.uniform(0.3, 1.2)
        a  = rng.uniform(0.12, 0.4)
        c.setFillColor(Color(r/255*220, r/255*205, r/255*175, alpha=a))
        c.circle(sx, sy, r, fill=1, stroke=0)

def screenshot(c, path, x, y, w, h, label="", radius=4):
    """Draw a screenshot with a gold border frame + optional label."""
    # Outer glow / border
    c.setFillColor(GOLD_DIM)
    c.roundRect(x-1.5, y-1.5, w+3, h+3, radius, fill=1, stroke=0)
    # Draw image
    c.drawImage(path, x, y, w, h, preserveAspectRatio=True, mask='auto')
    # Top-bar overlay to sell 'browser chrome' feel
    c.setFillColor(HexColor("#12100a"))
    c.rect(x, y+h-9, w, 9, fill=1, stroke=0)
    c.setFont("Helvetica-Bold", 5)
    c.setFillColor(GOLD)
    c.drawString(x+4, y+h-6.5, "AURA")
    # Caption label below
    if label:
        c.setFont("Helvetica", 5.5)
        c.setFillColor(DIM)
        c.drawCentredString(x+w/2, y-7, label)

# ── QR code ────────────────────────────────────────────────────────────────────
def make_qr(url, path="/home/claude/qr_code.png"):
    qr = qrcode.QRCode(version=2, box_size=6, border=2,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#aba372", back_color="#0e0e0e")
    img.save(path)
    return path

qr_path   = make_qr("https://aurahunch.space")
logo_path = "/home/claude/logo_gold.png"

SS = {
    "dashboard":    "/home/claude/ss_dashboard.png",
    "sensor":       "/home/claude/ss_sensor.png",
    "ai":           "/home/claude/ss_ai.png",
    "trends":       "/home/claude/ss_trends.png",
    "alerts":       "/home/claude/ss_alerts.png",
    "maintenance":  "/home/claude/ss_maintenance.png",
    "maintenance2": "/home/claude/ss_maintenance2.png",
    "twin":         "/home/claude/ss_twin.png",
}

out = "/home/claude/AURA_Brochure.pdf"
c   = canvas.Canvas(out, pagesize=(PAGE_W, PAGE_H))

# ══════════════════════════════════════════════════════════════════════════════
#  SIDE 1
# ══════════════════════════════════════════════════════════════════════════════
for pi in range(3):
    fill_panel(c, pi)
    stars(c, pi, seed=pi*13+5)
for pi in range(2):
    gradient(c, pi, PANEL_H, 1.8*inch, HexColor("#1c1a12"), BG, steps=20)

# Fold guides
c.setStrokeColor(GOLD_DIM); c.setLineWidth(0.3); c.setDash(3,4)
c.line(PANEL_W,0,PANEL_W,PANEL_H); c.line(2*PANEL_W,0,2*PANEL_W,PANEL_H)
c.setDash()

# ═══════════════════════════════════════
#  PANEL 3  —  FRONT COVER
# ═══════════════════════════════════════
pi = 2
CX = px(pi) + PANEL_W/2
gradient(c, pi, PANEL_H, PANEL_H, HexColor("#15120c"), BG, steps=32)
gold_bar(c, pi, PANEL_H-0.06*inch, 0.06*inch)

# Hexagon accents
c.setStrokeColor(GOLD_DIM); c.setLineWidth(0.4); c.setFillColor(HexColor("#1a1710"))
for hx_cx, hx_cy in [(CX-0.42*inch, PANEL_H-1.38*inch),
                      (CX+0.42*inch, PANEL_H-1.38*inch),
                      (CX, PANEL_H-1.78*inch)]:
    r = 0.27*inch
    pts = [(hx_cx+r*math.cos(math.radians(60*i-30)),
            hx_cy+r*math.sin(math.radians(60*i-30))) for i in range(6)]
    p = c.beginPath(); p.moveTo(*pts[0])
    for pt in pts[1:]: p.lineTo(*pt)
    p.close(); c.drawPath(p, fill=1, stroke=1)

# Logo
ls = 0.88*inch
c.drawImage(logo_path, CX-ls/2, PANEL_H-1.56*inch, ls, ls,
            mask='auto', preserveAspectRatio=True)

# Wordmark
txt(c, "AURA", CX, PANEL_H-2.02*inch, size=28, color=GOLD_LT, font="Helvetica-Bold", align="center")
c.setStrokeColor(GOLD); c.setLineWidth(0.8)
c.line(CX-0.72*inch, PANEL_H-2.15*inch, CX+0.72*inch, PANEL_H-2.15*inch)
txt(c, "ECLSS  PREDICTIVE  MAINTENANCE", CX, PANEL_H-2.35*inch,
    size=7, color=GOLD, font="Helvetica", align="center")

# Taglines
txt(c, "Mission-Critical Intelligence", CX, PANEL_H-2.8*inch,
    size=11, color=WHITE, font="Helvetica-Bold", align="center")
txt(c, "for the International Space Station", CX, PANEL_H-3.0*inch,
    size=8.5, color=TEXT, font="Helvetica", align="center")

wrap(c, "Real-time AI-driven monitoring, anomaly detection, and predictive "
        "maintenance for the ISS ECLSS life support systems.",
     px(pi)+0.24*inch, PANEL_H-3.38*inch, PANEL_W-0.48*inch,
     size=7.5, color=TEXT, lead=11)

# Feature pills
py_p = PANEL_H-4.1*inch
ppx = px(pi)+0.22*inch
ppx = pill(c, ppx, py_p, "AI-Powered")
ppx = pill(c, ppx, py_p, "Real-Time")
ppx = pill(c, ppx, py_p, "7 Modules")
py_p -= 0.18*inch
ppx = px(pi)+0.22*inch
ppx = pill(c, ppx, py_p, "LSTM + DQN")
ppx = pill(c, ppx, py_p, "Digital Twin")
ppx = pill(c, ppx, py_p, "Open Source")

draw_line_h(c, pi, PANEL_H-4.5*inch)

# Stats
for i, (val, lab) in enumerate([("7","ISS Modules"),("4","ML Models"),("20+","Sensors"),("1s","Tick Rate")]):
    sw = (PANEL_W-0.4*inch)/4
    sx = px(pi)+0.2*inch+i*sw+sw/2
    txt(c, val, sx, PANEL_H-5.0*inch, size=15, color=GOLD_LT, font="Helvetica-Bold", align="center")
    txt(c, lab, sx, PANEL_H-5.18*inch, size=6, color=DIM, font="Helvetica", align="center")

gold_bar(c, pi, 0.33*inch, 0.025*inch)
# [removed aurahunch footer]

# ═══════════════════════════════════════
#  PANEL 2  —  INTELLIGENCE LAYER
# ═══════════════════════════════════════
pi = 1
CX2 = px(pi) + PANEL_W/2

txt(c, "INTELLIGENCE  LAYER", CX2, PANEL_H-0.25*inch, size=6.5, color=GOLD_LT, font="Helvetica-Bold", align="center")
draw_line_h(c, pi, PANEL_H-0.35*inch)

y = PANEL_H - 0.62*inch
y = sec(c, pi, y, "AI Analyst", "Conversational System Intelligence")
for item in [
    "Natural language interface to all live ECLSS sensor data",
    "Powered by Ollama (local) or Groq cloud LLM backend",
    "Context-aware across all 7 ISS module locations",
    "Auto-appends live anomaly & sensor snapshot per query",
]:
    y = bullet(c, item, px(pi)+0.2*inch, y, PANEL_W-0.28*inch); y -= 1

# AI Analyst screenshot
y -= 5
draw_line_h(c, pi, y, color=GOLD_DIM, width=0.3); y -= 8
ss_w2 = PANEL_W - 0.3*inch
ss_h2 = ss_w2 * (300/440)
ss_x2 = px(pi) + 0.15*inch
screenshot(c, SS["ai"], ss_x2, y - ss_h2, ss_w2, ss_h2, label="AI Analyst — live fault diagnosis chat")
y = y - ss_h2 - 12

draw_line_h(c, pi, y); y -= 9

y = sec(c, pi, y, "Digital Twin", "3D Interactive ISS Visualization")
for item in [
    "Real-time Three.js 3D ISS model with anomaly overlays",
    "Color-coded module health tied to live sensor data",
    "Orbit-controls camera with anomaly pulse indicators",
]:
    y = bullet(c, item, px(pi)+0.2*inch, y, PANEL_W-0.28*inch); y -= 1

y -= 4
draw_line_h(c, pi, y, color=GOLD_DIM, width=0.3); y -= 8
ss_w3 = PANEL_W - 0.3*inch
_ti2=PILImage.open(SS["twin"]); _tw2,_th2=_ti2.size; _ti2.close()
ss_h3 = ss_w3 * _th2/_tw2
screenshot(c, SS["twin"], ss_x2, y - ss_h3, ss_w3, ss_h3, label="Digital Twin — 3D ISS model with live status")
y = y - ss_h3 - 8

gold_bar(c, pi, 0.33*inch, 0.025*inch)
# [removed aurahunch footer]

# ═══════════════════════════════════════
#  PANEL 1  —  THE TECHNOLOGY
# ═══════════════════════════════════════
pi = 0
CX1 = px(pi) + PANEL_W/2

txt(c, "THE  TECHNOLOGY", CX1, PANEL_H-0.25*inch, size=6.5, color=GOLD_LT, font="Helvetica-Bold", align="center")
draw_line_h(c, pi, PANEL_H-0.35*inch)

y = PANEL_H - 0.62*inch
y = sec(c, pi, y, "ECLSS Subsystems", "20+ Monitored Parameters")

subsys = [
    ("Atmosphere Revitalization", "O₂ · CO₂ · Humidity"),
    ("Oxygen Generation System",  "Output Rate · O₂ Purity"),
    ("Water Recovery System",     "Purity · Production Rate"),
    ("Temp & Humidity Control",   "Cabin Temperature"),
    ("Trace Contaminant Control", "NH₃ · H₂ · CO"),
    ("Pressure Control",          "Cabin Pressure"),
    ("Microbial Monitoring",      "Bacterial / Fungal Count"),
    ("Mass Spectrometer",         "N₂ · O₂ · CO₂ · CH₄"),
]
for name, params in subsys:
    txt(c, name,   px(pi)+0.2*inch, y,   size=7, color=GOLD_LT, font="Helvetica-Bold")
    txt(c, params, px(pi)+0.2*inch, y-8, size=6.5, color=DIM, font="Helvetica")
    y -= 18

y -= 2
draw_line_h(c, pi, y); y -= 9

y = sec(c, pi, y, "ML Pipeline", "Four-Model Ensemble")

models = [
    ("Isolation Forest",  "Unsupervised multi-sensor outlier detection"),
    ("Random Forest",     "Supervised fault type classification"),
    ("LSTM Network",      "Temporal drift & trend sequence modeling"),
    ("DQN Agent",         "RL-based adaptive alert suppression"),
]
for mname, mdesc in models:
    txt(c, mname, px(pi)+0.2*inch, y, size=7, color=GOLD_LT, font="Helvetica-Bold")
    y = wrap(c, mdesc, px(pi)+0.2*inch, y-9, PANEL_W-0.4*inch, size=6.5, color=DIM, lead=9); y -= 2

y -= 4
draw_line_h(c, pi, y, color=GOLD_DIM, width=0.3); y -= 8

# Sensor screenshot (real upload) — auto pixel ratio so border fits exactly
ss_w4 = PANEL_W - 0.3*inch
ss_x4 = px(pi) + 0.15*inch
_si = PILImage.open(SS["sensor"]); _sw, _sh = _si.size; _si.close()
ss_h4 = ss_w4 * _sh / _sw
screenshot(c, SS["sensor"], ss_x4, y - ss_h4, ss_w4, ss_h4, label="ECLSS System Overview — all 7 module live readings")
y = y - ss_h4 - 8

gold_bar(c, pi, 0.33*inch, 0.025*inch)
# [removed aurahunch footer]


# ══════════════════════════════════════════════════════════════════════════════
#  SIDE 2
# ══════════════════════════════════════════════════════════════════════════════
c.showPage()

for pi in range(3):
    fill_panel(c, pi)
    stars(c, pi, seed=pi*29+77)
for pi in range(3):
    gradient(c, pi, PANEL_H, 1.4*inch, HexColor("#1c1a12"), BG, steps=16)

c.setStrokeColor(GOLD_DIM); c.setLineWidth(0.3); c.setDash(3,4)
c.line(PANEL_W,0,PANEL_W,PANEL_H); c.line(2*PANEL_W,0,2*PANEL_W,PANEL_H)
c.setDash()

# ═══════════════════════════════════════
#  PANEL 4  —  BACK COVER / QR CODE
# ═══════════════════════════════════════
pi = 0
CX4 = px(pi) + PANEL_W/2
gold_bar(c, pi, PANEL_H-0.06*inch, 0.06*inch)

# Mini logo
logo_s = 0.45*inch
c.drawImage(logo_path, CX4-logo_s/2, PANEL_H-0.62*inch, logo_s, logo_s,
            mask='auto', preserveAspectRatio=True)
txt(c, "AURA", CX4, PANEL_H-0.76*inch, size=16, color=GOLD_LT, font="Helvetica-Bold", align="center")
txt(c, "ECLSS PREDICTIVE MAINTENANCE", CX4, PANEL_H-0.92*inch, size=6.5, color=GOLD, font="Helvetica", align="center")
draw_line_h(c, pi, PANEL_H-1.02*inch)

y_cl = wrap(c,
    "AURA brings space-grade AI to the front line of life support reliability. "
    "From real-time anomaly detection to conversational AI fault analysis, "
    "AURA gives mission teams the insight they need before the alarm sounds.",
    px(pi)+0.22*inch, PANEL_H-1.2*inch, PANEL_W-0.44*inch, size=7.5, color=TEXT, lead=11.5)

draw_line_h(c, pi, y_cl-4)

# QR code
qr_size = 1.45*inch
qr_x = CX4 - qr_size/2
qr_y = y_cl - 4 - 0.14*inch - qr_size
c.drawImage(qr_path, qr_x, qr_y, qr_size, qr_size,
            mask='auto', preserveAspectRatio=True)
txt(c, "SCAN TO VISIT", CX4, qr_y-0.14*inch, size=7, color=GOLD_LT, font="Helvetica-Bold", align="center")
txt(c, "aurahunch.space", CX4, qr_y-0.28*inch, size=8.5, color=GOLD_LT, font="Helvetica-Bold", align="center")

draw_line_h(c, pi, qr_y-0.4*inch)

y_info = qr_y - 0.6*inch
for label, value in [
    ("🌐  Website",     "aurahunch.space"),
    ("⚡  Runtime",     "Python · FastAPI · SQLite"),
    ("🤖  AI Backend",  "Ollama (local)"),
]:
    txt(c, label, px(pi)+0.22*inch, y_info, size=6.5, color=GOLD, font="Helvetica-Bold")
    txt(c, value, px(pi)+PANEL_W-0.22*inch, y_info, size=6.5, color=TEXT, font="Helvetica", align="right")
    y_info -= 12

draw_line_h(c, pi, y_info-2)
txt(c, "Intelligent Life Support.", CX4, y_info-16, size=8, color=WHITE, font="Helvetica-Bold", align="center")
txt(c, "Open-source · ISS ECLSS simulation", CX4, y_info-28, size=6.5, color=DIM, font="Helvetica", align="center")

gold_bar(c, pi, 0.33*inch, 0.025*inch)
# [removed aurahunch footer]

# ═══════════════════════════════════════
#  PANEL 5  —  HOW IT WORKS
# ═══════════════════════════════════════
pi = 1
CX5 = px(pi) + PANEL_W/2

txt(c, "HOW  IT  WORKS", CX5, PANEL_H-0.25*inch, size=6.5, color=GOLD_LT, font="Helvetica-Bold", align="center")
draw_line_h(c, pi, PANEL_H-0.35*inch)

steps = [
    ("01", "Data Generation",
     "Background engine samples all 7 ISS locations every second with realistic sensor simulation and optional fault injection."),
    ("02", "ML Inference",
     "Four-model ensemble runs each tick: Isolation Forest detects outliers, Random Forest classifies fault type, LSTM tracks drift, DQN controls alerting."),
    ("03", "Alert Triggering",
     "After N consecutive anomalous ticks, AURA raises a severity-graded alert with location, parameter, and recommended action."),
    ("04", "Trend Analysis",
     "Built-in trend detector identifies slopes and deviations across any sensor or module before alert thresholds are reached."),
    ("05", "AI Query",
     "Natural language question triggers a live sensor snapshot that is appended to an LLM prompt for contextual fault diagnosis."),
    ("06", "Maintenance Action",
     "Scheduler aggregates MTBF intervals and calibration drift, giving operators a clear timeline of upcoming ECLSS service tasks."),
]

y = PANEL_H - 0.6*inch
for num, title, desc in steps:
    c.setFillColor(GOLD_DIM)
    c.circle(px(pi)+0.30*inch, y-1, 7, fill=1, stroke=0)
    txt(c, num, px(pi)+0.30*inch, y-3.5, size=6.5, color=GOLD_LT, font="Helvetica-Bold", align="center")
    txt(c, title, px(pi)+0.46*inch, y, size=7.5, color=WHITE, font="Helvetica-Bold")
    y = wrap(c, desc, px(pi)+0.46*inch, y-10, PANEL_W-0.58*inch, size=6.5, color=DIM, lead=9.5)
    y -= 3
    if num != "06":
        c.setFillColor(BORDER)
        c.rect(px(pi)+0.28*inch, y+1, 0.04*inch, 4, fill=1, stroke=0)
        y -= 3

y -= 4
draw_line_h(c, pi, y, color=GOLD_DIM, width=0.3); y -= 8

# Maintenance screenshot (real upload) — auto pixel ratio
ss_wt = PANEL_W - 0.3*inch
ss_xt = px(pi) + 0.15*inch
_mi = PILImage.open(SS["maintenance"]); _mw,_mh=_mi.size; _mi.close()
ss_ht = ss_wt * _mh / _mw
screenshot(c, SS["maintenance"], ss_xt, y - ss_ht, ss_wt, ss_ht, label="Maintenance Schedule — calibration tracking & MTBF intervals")
y = y - ss_ht - 10

# Monitored locations
draw_line_h(c, pi, y, color=GOLD_DIM, width=0.3); y -= 9
txt(c, "MONITORED LOCATIONS", px(pi)+PANEL_W/2, y, size=6.5, color=GOLD, font="Helvetica-Bold", align="center"); y -= 12
_locs = ["JLP & JPM", "Node 2", "Columbus", "US Lab", "Cupola", "Node 1", "Joint Airlock"]
txt(c, "  ·  ".join(_locs[:4]), px(pi)+PANEL_W/2, y, size=6, color=TEXT, font="Helvetica", align="center"); y -= 10
txt(c, "  ·  ".join(_locs[4:]), px(pi)+PANEL_W/2, y, size=6, color=TEXT, font="Helvetica", align="center"); y -= 10

# Dashboard screenshot
draw_line_h(c, pi, y, color=GOLD_DIM, width=0.3); y -= 8
_ss5w = PANEL_W - 0.3*inch
_ss5x = px(pi) + 0.15*inch
_di2 = PILImage.open(SS["dashboard"]); _dw2,_dh2=_di2.size; _di2.close()
_ss5h = _ss5w * _dh2/_dw2
screenshot(c, SS["dashboard"], _ss5x, y - _ss5h, _ss5w, _ss5h, label="ECLSS System Overview — all 7 module live readings")
y = y - _ss5h - 8

gold_bar(c, pi, 0.33*inch, 0.025*inch)
# [removed aurahunch footer]

# ═══════════════════════════════════════
#  PANEL 6  —  KEY FEATURES
# ═══════════════════════════════════════
pi = 2
CX6 = px(pi) + PANEL_W/2

txt(c, "KEY  FEATURES", CX6, PANEL_H-0.25*inch, size=6.5, color=GOLD_LT, font="Helvetica-Bold", align="center")
draw_line_h(c, pi, PANEL_H-0.35*inch)

y = PANEL_H - 0.60*inch

y = sec(c, pi, y, "Real-Time Dashboard", "Live Sensor Monitoring")
for f in [
    "Live WebSocket sensor readings across all locations",
    "Sensor drill-down with 60/100/200/1000-point windows",
    "Alert badge counts on nav tabs with auto-refresh",
]:
    y = bullet(c, f, px(pi)+0.2*inch, y, PANEL_W-0.28*inch); y -= 1

y -= 4; draw_line_h(c, pi, y, color=GOLD_DIM, width=0.3); y -= 8

# Alerts screenshot
ss_wa = PANEL_W - 0.3*inch
ss_ha = ss_wa * (260/440)
ss_xa = px(pi) + 0.15*inch
screenshot(c, SS["alerts"], ss_xa, y - ss_ha, ss_wa, ss_ha, label="Alert Panel — severity levels with acknowledge workflow")
y = y - ss_ha - 10

draw_line_h(c, pi, y); y -= 9

y = sec(c, pi, y, "Alert System", "Configurable & Actionable")
for f in [
    "NOMINAL · ADVISORY · WARNING · CRITICAL severity grades",
    "Consecutive-tick threshold & alert cooldown configuration",
    "Unacknowledged filter and bulk-acknowledge workflow",
]:
    y = bullet(c, f, px(pi)+0.2*inch, y, PANEL_W-0.28*inch); y -= 1

y -= 4; draw_line_h(c, pi, y); y -= 9

y = sec(c, pi, y, "Maintenance Scheduler", "MTBF-Driven Planning")
for f in [
    "Subsystem replacement intervals from ISS MTBF data",
    "Sensor calibration drift tracking with status flags",
    "Mission Elapsed Time integrated timeline",
]:
    y = bullet(c, f, px(pi)+0.2*inch, y, PANEL_W-0.28*inch); y -= 1

y -= 4; draw_line_h(c, pi, y, color=GOLD_DIM, width=0.3); y -= 8

# Subsystem Replacement Schedule screenshot (real upload)
_ss6w = PANEL_W - 0.3*inch
_ss6x = px(pi) + 0.15*inch
_m2i = PILImage.open(SS["maintenance2"]); _m2w,_m2h=_m2i.size; _m2i.close()
_ss6h = _ss6w * _m2h/_m2w
screenshot(c, SS["maintenance2"], _ss6x, y - _ss6h, _ss6w, _ss6h, label="Subsystem Replacement Schedule — MTBF-based service cards")
y = y - _ss6h - 8

gold_bar(c, pi, 0.33*inch, 0.025*inch)
# [removed aurahunch footer]

c.save()
print(f"✅  Saved: {out}")
