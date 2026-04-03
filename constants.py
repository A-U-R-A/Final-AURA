### Data Generation ###
DATA_GENERATION_INTERVAL = 0.0001  # seconds between ticks
DEFAULT_DATA_POINT_AMOUNT = 50
DATA_ROWS_IN_QUEUE = 5_000

### Paths ###
DATABASE_PATH = "data/aura.db"
IF_MODEL_PATH = "models/isolationForestModel.joblib"
RF_MODEL_PATH = "models/randomForestModel.joblib"

### ISS Locations ###
LOCATIONS = [
    "JLP & JPM",
    "Node 2",
    "Columbus",
    "US Lab",
    "Cupola",
    "Node 1",
    "Joint Airlock",
]

# Pixel positions within the digital twin SVG display area (approx 900x700)
LOCATION_POSITIONS = {
    "JLP & JPM":      (118, 211),
    "Node 2":         (492, 263),
    "Columbus":       (773, 223),
    "US Lab":         (506, 465),
    "Cupola":         (225, 625),
    "Node 1":         (499, 631),
    "Joint Airlock":  (785, 624),
}

### Subsystem → Parameter mapping ###
SUBSYSTEM_PARAMETERS = {
    "Atmosphere Revitalization System": [
        "O2 partial pressure",
        "CO2 partial pressure",
        "Humidity",
    ],
    "Oxygen Generation System": [
        "O2 output rate (generator)",
        "O2 purity (generator)",
    ],
    "Water Recovery System": [
        "Water purity",
        "Production rate (water recovery system)",
    ],
    "Temperature and Humidity Control": [
        "Temperature",
    ],
    "Trace Contaminant Control": [
        "NH3",
        "H2 (%)",
        "H2 (ppm)",
        "CO",
    ],
    "Air Circulation/Ventilation": [
        "Airflow rate",
    ],
    "Pressure Control": [
        "Cabin pressure",
    ],
    "Microbial Monitoring": [
        "Bacterial/fungal count",
    ],
    "Mass Spectrometer Module": [
        "N2",
        "O2",
        "CO2",
        "CH4",
        "H2O",
    ],
}

### Physical limits (hard clamps) ###
PHYSICAL_LIMITS = {
    "O2 partial pressure":                  (0.0, 100.0),
    "CO2 partial pressure":                 (0.0, 10.0),
    "Humidity":                             (0.0, 100.0),
    "O2 output rate (generator)":           (0.0, 15.0),
    "O2 purity (generator)":               (0.0, 100.0),
    "Water purity":                         (0.0, 20.0),
    "Production rate (water recovery system)": (0.0, 100.0),
    "Temperature":                          (-20.0, 60.0),
    "NH3":                                  (0.0, 500.0),
    "H2 (%)":                               (0.0, 100.0),
    "CO":                                   (0.0, 1000.0),
    "Airflow rate":                         (0.0, 5.0),
    "Cabin pressure":                       (0.0, 25.0),
    "Bacterial/fungal count":              (0.0, 10000.0),
    "N2":                                   (0.0, 100.0),
    "O2":                                   (0.0, 100.0),
    "CO2":                                  (0.0, 10.0),
    "CH4":                                  (0.0, 50000.0),
    "H2 (ppm)":                             (0.0, 100000.0),
    "H2O":                                  (0.0, 100.0),
}

### Nominal operating ranges ###
# Sources: NASA-STD-3001, JSC-20584 SMAC limits, ISS ECLSS operational data
PARAMETER_NOMINAL_RANGES = {
    "O2 partial pressure":                  (19.5, 23.1),   # % vol (NASA-STD-3001 Vol.1 Table 6.2.1: 19.5-23.1% O2 by volume; ~2.87-3.40 psia at 14.7 psia total)
    "CO2 partial pressure":                 (0.13, 0.70),   # % atm; 0.13%=1mmHg floor, 0.70%=5.3mmHg caution limit
    "Humidity":                             (0.25, 0.70),   # % RH; NASA-STD-3001 max = 70%
    "O2 output rate (generator)":           (2.0, 9.0),     # kg/day; OGS rated 5.4 kg/day nominal
    "O2 purity (generator)":               (0.99, 1.0),    # fraction; electrolyzer target >99%
    "Water purity":                         (0.0, 3.0),     # mg/L TOC; potable limit <3 mg/L
    "Production rate (water recovery system)": (30.0, 35.0), # kg/day; WPA design 30-35 kg/day
    "Temperature":                          (18.0, 27.0),   # °C; NASA-STD-3001 comfort range
    "NH3":                                  (0.0, 1.0),     # ppm; nominal background <1 ppm; 180-day SMAC=2 mg/m3 (~2.6 ppm); caution at 10 ppm
    "H2 (%)":                               (0.0, 0.1),     # %; OGS crossover alarm at 2%; H2 >4% LEL risk
    "CO":                                   (0.0, 5.0),     # ppm; nominal background <5 ppm; 180-day SMAC=10 ppm; 7-day SMAC=15 ppm
    "Airflow rate":                         (0.1, 1.0),     # m/s; IMV/CCAA target 0.1–1.0 m/s
    "Cabin pressure":                       (14.0, 14.9),   # psia; ISS ops 14.0–14.9 (14.7 target)
    "Bacterial/fungal count":              (0.0, 50.0),    # CFU/mL; ISS microbial limit 50 CFU/mL
    "N2":                                   (0.75, 0.80),   # fraction; balance gas to total pressure
    "O2":                                   (19.5, 23.5),   # % (mass spec channel)
    "CO2":                                  (0.13, 0.70),   # % (mass spec channel, mirrors CO2 partial pressure)
    "CH4":                                  (0.0, 10.0),    # ppm; Sabatier byproduct, SMAC-1 = 5300 ppm
    "H2 (ppm)":                             (0.0, 10.0),    # ppm; dissolved/trace H2 monitoring
    "H2O":                                  (0.20, 0.60),   # % RH (mass spec humidity channel)
}

### Parameter units (for UI display) ###
PARAMETER_UNITS = {
    "O2 partial pressure":                  "%",
    "CO2 partial pressure":                 "%",
    "Humidity":                             "%",
    "O2 output rate (generator)":           "kg/day",
    "O2 purity (generator)":               "%",
    "Water purity":                         "mg/L",
    "Production rate (water recovery system)": "kg/day",
    "Temperature":                          "°C",
    "NH3":                                  "ppm",
    "H2 (%)":                               "%",
    "CO":                                   "ppm",
    "Airflow rate":                         "m/s",
    "Cabin pressure":                       "psi",
    "Bacterial/fungal count":              "CFU/mL",
    "N2":                                   "%",
    "O2":                                   "%",
    "CO2":                                  "%",
    "CH4":                                  "ppm",
    "H2 (ppm)":                             "ppm",
    "H2O":                                  "% RH",
}

### Cross-parameter physical correlations ###
PARAMETER_CORRELATIONS = [
    {"parameters": ["O2 partial pressure", "O2 output rate (generator)"],
     "correlation": "OGA monitors O2 partial pressure; increases production when pressure drops below threshold."},
    {"parameters": ["Cabin pressure", "O2 partial pressure", "CO2 partial pressure", "N2", "H2O"],
     "correlation": "Total cabin pressure = sum of all gas partial pressures (Dalton's Law)."},
    {"parameters": ["CO2 partial pressure", "Airflow rate"],
     "correlation": "Constant airflow prevents localized CO2 accumulation in microgravity."},
    {"parameters": ["Humidity", "Bacterial/fungal count"],
     "correlation": "RH > 80% facilitates rapid microbial growth in spacecraft dust."},
    {"parameters": ["Temperature", "Humidity"],
     "correlation": "Coupled via CCAA — regulates temperature by cooling below dew point."},
    {"parameters": ["Temperature", "Production rate (water recovery system)"],
     "correlation": "WRS production rate is proportional to brine temperature."},
    {"parameters": ["Water purity", "Production rate (water recovery system)"],
     "correlation": "Higher recovery rates increase TOC/metals concentration in brine."},
    {"parameters": ["O2 purity (generator)", "H2 (ppm)", "H2 (%)"],
     "correlation": "H2 sensors monitor O2 stream for cell stack leaks."},
    {"parameters": ["CO2", "H2 (%)", "CH4", "H2O"],
     "correlation": "Linked via the Sabatier process: CO2 + H2 → H2O + CH4."},
    {"parameters": ["NH3", "Temperature"],
     "correlation": "NH3 used as external thermal medium; increase indicates heat exchange leak."},
    {"parameters": ["N2", "Cabin pressure"],
     "correlation": "N2 partial pressure tracks overboard cabin air leakage."},
    {"parameters": ["CO", "CO2"],
     "correlation": "Both metabolic byproducts monitored against SMACs limits."},
]

### Fault definitions and parameter impacts ###
FAULT_IMPACT_SEVERITY = {
    "Cabin Leak": {
        "impacts": {
            "Cabin pressure":    -0.08,
            "N2":                -0.07,
            "O2 partial pressure": -0.05,
            "CO2":               -0.04,
            "Humidity":          -0.03,
            "Temperature":       -0.02,
            "Airflow rate":       0.02,
        }
    },
    "O2 Generator Failure": {
        "impacts": {
            "O2 output rate (generator)": -0.10,
            "O2 partial pressure":        -0.08,
            "O2 purity (generator)":     -0.05,
            "H2 (ppm)":                   0.04,
            "Cabin pressure":            -0.02,
            "CO2":                        0.02,
        }
    },
    "O2 Leak": {
        "impacts": {
            "O2 partial pressure": -0.09,
            "Cabin pressure":      -0.05,
            "O2":                  -0.07,
            "Airflow rate":         0.01,
        }
    },
    "CO2 Scrubber Failure": {
        "impacts": {
            "CO2 partial pressure": 0.10,
            "CO2":                  0.09,
            "O2 partial pressure": -0.01,
            "Temperature":          0.02,
        }
    },
    "CHX Failure": {
        "impacts": {
            "Humidity":              0.10,
            "Temperature":           0.07,
            "Bacterial/fungal count": 0.08,
            "Airflow rate":          -0.05,
        }
    },
    "Water Processor Failure": {
        "impacts": {
            "Water purity":                        -0.09,
            "Production rate (water recovery system)": -0.08,
            "Bacterial/fungal count":               0.06,
            "H2O":                                 -0.04,
        }
    },
    "Trace Contaminant Filter Saturation": {
        # Coefficients = nominal_spans/hr; NH3 span now 1 ppm → need ~3 to get 3.75 ppm/hr
        # CO span now 5 ppm → need ~0.5 to get 3.125 ppm/hr (alarm at 50 ppm in ~16 h)
        "impacts": {
            "NH3":      3.0,    # was 0.07; span shrank from 25→1 ppm, scaled to maintain ~3.75 ppm/hr
            "CH4":      0.06,
            "CO":       0.5,    # was 0.05; span shrank from 50→5 ppm, scaled to maintain ~3 ppm/hr
            "H2 (ppm)": 0.04,
        }
    },
    "NH3 Coolant Leak": {
        # NH3 span now 1 ppm; coeff 15 → 15 * 1 * 1.25 = 18.75 ppm/hr → 25 ppm caution in ~1.3 h ✓
        "impacts": {
            "NH3":           15.0,  # was 0.10; scaled for new 1-ppm nominal span
            "Temperature":    0.08,
            "O2":            -0.03,
            "Cabin pressure":  0.02,
        }
    },
}

### Remediation actions ###
ACTIONS_TO_TAKE = [
    "No Action Needed",
    "Use Sealant to Close Leak",
    "Remove gas bubbles from O2 generator",
    "Close Oxygen Isolation Valve",
    "Remove and Replace Air Selector Valves",
    "Replace CCAA Heat Exchanger",
    "Replace MF beds or Ion Exchange Beds",
    "Replace Active Charcoal Bed",
    "Close Hatch and replace external pump module",
    "Use Redundant Sensors on Pump",
    "Reopen Reference Gas Valve",
]

ACTIONS_TO_FAULT = {
    action: fault
    for action, fault in zip(ACTIONS_TO_TAKE[1:], FAULT_IMPACT_SEVERITY.keys())
}

### How many hours of precursor signals appear before each fault becomes critical ###
# Calibrated to physics-based drift rates after time-scaling fix in data_generator.py.
# These represent the detection window, not time-to-catastrophic-failure.
FAULT_PRECURSOR_HOURS = {
    "Cabin Leak":                            8.0,   # ~0.07 psia/hr drop; exits nominal in ~6-8 h
    "O2 Generator Failure":                 24.0,   # OGS MTBF 3,104 h observed; degradation ~24 h precursor
    "O2 Leak":                               4.0,   # O2 partial pressure drops quickly; alarm in 3-5 h
    "CO2 Scrubber Failure":                  4.0,   # CO2 rises 1.5-2 mmHg/hr; caution at 5.3 mmHg → ~2-3 h
    "CHX Failure":                          12.0,   # Humidity/temp rise; CHX degradation over 10-14 h
    "Water Processor Failure":              48.0,   # WPA MTBF 3,850 h; purity degrades over days
    "Trace Contaminant Filter Saturation":  72.0,   # Filter loads slowly; NH3/CO rise over 3+ days
    "NH3 Coolant Leak":                      2.0,   # Fast detection; NH3 25 ppm caution in <2 h for major leak
}

### Alert configuration per fault type ─────────────────────────────────────────
# Each fault has customizable alert sensitivity: how many consecutive anomalous
# readings trigger an alert, and how long before the next alert can fire.
# Adjust these to make certain faults alert faster/slower as needed.
FAULT_ALERT_CONFIG = {
    "Cabin Leak": {
        "min_consecutive": 25,    # ~20 seconds at 1 Hz; moderate detection urgency
        "cooldown_seconds": 300,  # 5 min between repeats
    },
    "O2 Generator Failure": {
        "min_consecutive": 30,    # ~30 sec; OGS failures are gradual, can wait a bit longer
        "cooldown_seconds": 600,  # 10 min between repeats (degradation is slow)
    },
    "O2 Leak": {
        "min_consecutive": 25,    # ~15 sec; O2 pressure drops quickly, need fast alert
        "cooldown_seconds": 300,  # 5 min between repeats
    },
    "CO2 Scrubber Failure": {
        "min_consecutive": 35,    # ~15 sec; CO2 rises fast, early warning needed
        "cooldown_seconds": 300,  # 5 min between repeats
    },
    "CHX Failure": {
        "min_consecutive": 25,    # ~25 sec; humidity/temp rise is gradual
        "cooldown_seconds": 450,  # 7.5 min between repeats
    },
    "Water Processor Failure": {
        "min_consecutive": 30,    # ~40 sec; water purity degrades over hours, can be patient
        "cooldown_seconds": 900,  # 15 min between repeats
    },
    "Trace Contaminant Filter Saturation": {
        "min_consecutive": 25,    # ~50 sec; filter saturation is very slow
        "cooldown_seconds": 1200, # 20 min between repeats
    },
    "NH3 Coolant Leak": {
        "min_consecutive": 25,    # ~10 sec; NH3 spike is fast and dangerous
        "cooldown_seconds": 300,  # 5 min between repeats
    },
}

### Numeric parameter correlations for Cholesky correlated noise (training) ###
# Keys are (param_a, param_b), value is Pearson r in [-1, 1]
PARAMETER_CORRELATION_MATRIX = {
    ("O2 partial pressure",  "CO2 partial pressure"):       -0.82,
    ("O2 partial pressure",  "N2"):                         -0.60,
    ("Temperature",          "Humidity"):                   +0.65,
    ("Cabin pressure",       "O2 partial pressure"):        +0.45,
    ("O2 output rate (generator)", "O2 partial pressure"):  +0.55,
    ("CO2 partial pressure", "CO2"):                        +0.78,
    ("NH3",                  "Temperature"):                +0.40,
    ("CO",                   "CO2 partial pressure"):       +0.55,
    ("H2 (%)",               "H2 (ppm)"):                   +0.90,
    ("Humidity",             "Bacterial/fungal count"):     +0.50,
    ("Water purity",         "Production rate (water recovery system)"): -0.45,
}

### Long-term nominal aging rates (per simulated day) — normal wear, not faults ###
NOMINAL_AGING_PER_DAY = {
    "CO2 partial pressure":                  +0.001,   # scrubber slowly loads
    "Water purity":                          +0.002,   # WRS purity slowly degrades
    "O2 output rate (generator)":            -0.001,   # OGS membrane slowly degrades
    "Production rate (water recovery system)": -0.0003, # WRS pump slowly declines
}

### Per-sensor noise sigma (fraction of nominal span, 1-sigma) ###
# Sources: MCA spectrometer ±0.15 mmHg CO2; galvanic O2 ±0.1-0.5%;
#          PCA pressure ±0.01 psi; TOCA TOC ±25%; temperature ±0.2°C
SENSOR_NOISE_SIGMA = {
    "O2 partial pressure":                  0.015,  # ±0.15% of 3.6-span → ~±0.054 psia
    "CO2 partial pressure":                 0.04,   # MCA: ±0.15 mmHg = ±0.02%; generous for sim
    "Humidity":                             0.025,  # CCAA RH sensor ±2-3%
    "O2 output rate (generator)":           0.02,
    "O2 purity (generator)":               0.005,  # electrolyzer purity sensor ±0.5%
    "Water purity":                         0.05,   # TOCA ±25% at low concentrations
    "Production rate (water recovery system)": 0.02,
    "Temperature":                          0.01,   # ±0.2°C over 9°C span
    "NH3":                                  0.03,   # MCA NH3 channel ±0.03 ppm
    "H2 (%)":                               0.02,
    "CO":                                   0.04,   # MCA CO channel ±0.2 ppm
    "Airflow rate":                         0.03,
    "Cabin pressure":                       0.005,  # PCA ±0.01 psi over 0.9-span
    "Bacterial/fungal count":              0.10,   # culture count is inherently noisy
    "N2":                                   0.01,
    "O2":                                   0.015,
    "CO2":                                  0.04,
    "CH4":                                  0.03,
    "H2 (ppm)":                             0.03,
    "H2O":                                  0.025,
}

### Sensor/subsystem mean time between failures (hours, observed ISS data) ###
# Sources: NASA post-flight ECLSS anomaly reports, ICES papers, JSC-62802
# Key correction notes:
#   OGS: actual 3,104h observed (pre-flight estimate was 8,437h — 63% worse)
#   WPA: actual ~3,850h (close to original estimate; internal sieve beds replaced ~annually)
#   CDRA: ~4,380h (~6-month major service interval per SSP-57003 maintenance plan)
#   CCAA CHX: hydrophilic coating degrades in 2-4 years; 17,520h (2yr) is a realistic mean
#   TCCS: NO flight failures in first 20+ years; charcoal/LiOH bed life ~2.25 years (19,746h)
#         Sampling every 90 days does NOT mean replacement every 90 days
#   EATCS NH3: loop spec is 7 lbm/yr allowable loss; major leak events extremely rare (~2yr mean)
#   Cabin Leak: structural integrity maintained 5+ years; 43,800h (5yr) is conservative
#   O2 Plumbing Leak: orbital maintenance interval ~1 year for fittings/seals inspection
SENSOR_MTBF_HOURS = {
    "O2 Generator Failure":                3104,   # OGS actual MTBF (vs 8,437h pre-flight estimate)
    "Water Processor Failure":             3850,   # WPA actual MTBF; sieve beds ~annually
    "CO2 Scrubber Failure":                4380,   # CDRA ~6-month major service per SSP-57003
    "CHX Failure":                        17520,   # CCAA CHX hydrophilic coating: ~2yr mean (2-4yr range)
    "Trace Contaminant Filter Saturation": 19746,  # TCCS charcoal bed life ~2.25 years; no flight failures
    "NH3 Coolant Leak":                   17520,   # EATCS loop; major leak ~2-year mean
    "Cabin Leak":                          43800,  # Structural leak: very rare (5-year mean)
    "O2 Leak":                             8760,   # O2 plumbing: ~annual inspection/seal replacement
}

### Maintenance recommendations per fault/subsystem ###
# maintenance_type: "condition_based" = inspect/replace when condition threshold met
#                   "calendar_based"  = replace on fixed schedule regardless of condition
# source: primary reference for the interval
MAINTENANCE_RECOMMENDATIONS = {
    "O2 Generator Failure": {
        "subsystem":          "Oxygen Generation Assembly (OGA/OGS)",
        "maintenance_type":   "condition_based",
        "primary_action":     "Inspect and replace cell stack modules; purge gas bubbles from electrolysis cells; check H2 separator membrane integrity.",
        "interval_note":      "MTBF 3,104h actual (vs 8,437h pre-flight estimate). Schedule R&R at 75% life. Do not delay — OGS failures directly impact crew O2 budget.",
        "smac_trigger":       "O2 partial pressure <19.5% or H2 >2% in O2 stream triggers immediate inspection.",
        "source":             "NASA-TM-2022-217643; ICES-2018-096",
    },
    "Water Processor Failure": {
        "subsystem":          "Water Recovery Assembly (WPA/WRS)",
        "maintenance_type":   "calendar_based",
        "primary_action":     "Replace iodinated resin sieve beds (~annually); replace multifiltration (MF) beds when TOC >3 mg/L; inspect ion exchange beds.",
        "interval_note":      "MTBF ~3,850h; internal sieve beds replaced on ~annual schedule per SSP-57003 regardless of condition. MF beds are condition-based (TOC monitoring).",
        "smac_trigger":       "TOCA TOC >3 mg/L requires immediate water processing halt and MF bed replacement.",
        "source":             "ICES-2020-314; JSC-62802 WRS maintenance plan",
    },
    "CO2 Scrubber Failure": {
        "subsystem":          "Carbon Dioxide Removal Assembly (CDRA)",
        "maintenance_type":   "calendar_based",
        "primary_action":     "Replace desiccant/sorbent beds (Zeolite 13X) on 6-month cycle; inspect selector valves; check heater elements.",
        "interval_note":      "MTBF ~4,380h. Major service every ~6 months per SSP-57003. Selector valve failures are most common failure mode — inspect seals each service.",
        "smac_trigger":       "CO2 partial pressure >0.70% (5.3 mmHg) triggers emergency CDRA inspection; >1.0% is crew health limit.",
        "source":             "SSP-57003 Table 3.2; ICES-2019-234",
    },
    "CHX Failure": {
        "subsystem":          "Common Cabin Air Assembly — Condensing Heat Exchanger (CCAA/CHX)",
        "maintenance_type":   "condition_based",
        "primary_action":     "Inspect and re-apply hydrophilic coating on CHX surfaces; clean microbial growth; check condensate separator disk.",
        "interval_note":      "Coating degrades over 2-4 years (MTBF ~17,520h / 2yr mean). Inspect annually; replace when humidity control efficiency drops >10% or condensate throughput declines.",
        "smac_trigger":       "Cabin RH >70% sustained >48h or temperature >27 deg C triggers CHX inspection.",
        "source":             "ICES-2021-188; NASA-STD-3001 Vol.1 Table 6.2.1",
    },
    "Trace Contaminant Filter Saturation": {
        "subsystem":          "Trace Contaminant Control System (TCCS)",
        "maintenance_type":   "condition_based",
        "primary_action":     "Replace activated charcoal bed and LiOH bed when NH3 or VOC breakthrough detected. Sample charcoal every 90 days to track loading — replace only when sampling confirms saturation (~2.25yr typical).",
        "interval_note":      "Charcoal bed life ~19,746h (2.25yr). NO TCCS flight failures in 20+ years of ISS ops. 90-day sampling cycle does NOT mean 90-day replacement — previous MTBF of 2,160h was incorrect.",
        "smac_trigger":       "NH3 >2.6 ppm (180-day SMAC) or any VOC above SMAC-1 limits triggers immediate TCCS bed inspection and possible early replacement.",
        "source":             "ICES-2022-289; JSC-20584 SMAC Rev.E",
    },
    "NH3 Coolant Leak": {
        "subsystem":          "External Active Thermal Control System (EATCS) — NH3 coolant loops",
        "maintenance_type":   "condition_based",
        "primary_action":     "Inspect pump module fittings and quick-disconnects; replace external pump module (EPM) if leak confirmed via EVA or mass spectrometry trending. Allowable loss spec: 7 lbm/yr.",
        "interval_note":      "Major coolant leak events rare (~17,520h MTBF / 2yr mean). NH3 is toxic — any confirmed increase above 10 ppm cabin NH3 requires immediate source isolation and crew PPE.",
        "smac_trigger":       "NH3 >10 ppm cabin concentration requires PPE and immediate NH3 isolation procedure; >25 ppm is emergency evacuation threshold.",
        "source":             "SSP-57003 EATCS maintenance; JSC-20584 NH3 SMAC",
    },
    "Cabin Leak": {
        "subsystem":          "Pressure Control System — cabin structural integrity",
        "maintenance_type":   "condition_based",
        "primary_action":     "Perform systematic leak isolation (close hatches, isolate modules). Use ultrasonic leak detector. Seal with RTV sealant or replace failed penetration seal/fitting.",
        "interval_note":      "Structural leaks very rare (~43,800h / 5yr MTBF). Monitor cabin pressure trend continuously. A loss rate >0.1 psia/hr requires immediate action.",
        "smac_trigger":       "Cabin pressure <14.0 psia or pressure drop rate >0.05 psia/hr sustained over 2h triggers leak investigation protocol.",
        "source":             "ISS System Handbook Vol.3; ICES-2019-102",
    },
    "O2 Leak": {
        "subsystem":          "O2 distribution plumbing — High Pressure Gas System (HPGS)",
        "maintenance_type":   "calendar_based",
        "primary_action":     "Inspect O2 supply line fittings, quick-disconnects, and isolation valve seals annually. Close O2 isolation valve if leak confirmed; transition to HPGS backup.",
        "interval_note":      "Annual inspection cycle (~8,760h). O2-enriched atmosphere is a fire hazard — O2 partial pressure >23.1% requires immediate O2 flow isolation.",
        "smac_trigger":       "O2 partial pressure >23.5% or O2 mass-spec channel rising trend >0.5%/hr triggers isolation valve closure.",
        "source":             "SSP-57003 HPGS maintenance; NASA-STD-3001 fire risk O2 limits",
    },
}

### Sensor calibration drift rate (fraction of nominal span per week, 1-sigma) ###
# Sources: ICES-2019-234 (MCA), ICES-2022-289 (galvanic O2), JSC-62802 cal intervals
# Key correction notes:
#   CO2 (MCA ECV): ~0.19%/6wk = 0.032%/wk; MCA cal interval 6 weeks (extendable to 12)
#   O2 galvanic cells: 2-5%/month linearity loss = 0.5-1.25%/wk — was severely underestimated
#     at 0.001 (0.1%/wk); corrected to 0.005 (0.5%/wk, conservative end of range)
#   H2 sensors (MCA): ~4% over 72 weeks = 0.056%/wk
#   NH3 (MCA): slow drift, field data ~0.05%/wk
#   CO (MCA): similar to NH3 channel, ~0.05%/wk
#   TOCA water quality optical sensor: ~0.2%/wk aging baseline
CALIBRATION_DRIFT_PER_WEEK = {
    "CO2 partial pressure":   0.00032,  # MCA ECV ~0.032%/wk (ICES-2019-234); cal every 6 weeks
    "CO2":                    0.00032,  # same MCA channel
    "O2 partial pressure":    0.005,    # galvanic cell 2-5%/month = 0.5-1.25%/wk; use 0.5%/wk
    "O2":                     0.005,    # same galvanic sensor
    "Water purity":           0.002,    # TOCA optical aging ~0.2%/wk
    "NH3":                    0.0005,   # MCA NH3 channel ~0.05%/wk
    "CO":                     0.0005,   # MCA CO channel ~0.05%/wk
    "H2 (%)":                 0.00056,  # MCA H2 channel: ~4% drift over 72 wk = 0.056%/wk
    "H2 (ppm)":               0.00056,  # same H2 sensor (ppm scale)
}
