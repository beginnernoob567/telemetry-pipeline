# telemetry_device_sim/config.py
import os

# ── Redpanda ──────────────────────────────────────────────────────────────────
REDPANDA_BROKERS = os.getenv("REDPANDA_BROKERS", "localhost:9092")

TOPICS = {
    "CHIL": "telemetry.cooling-units",
    "PUMO": "telemetry.pump-motors",
    "failures": "telemetry.failures",
}

# ── Timing ────────────────────────────────────────────────────────────────────
BASE_TICK_MS      = 200   # base emission interval in milliseconds
JITTER_MS         = 30    # ± jitter per device, keeps ticks unsynchronised

# ── Fleet definition ──────────────────────────────────────────────────────────
# Format: (device_id, scenario)
FLEET = [
    # Chillers
    ("IN_BLR_CHIL_01", "healthy"),
    ("IN_BLR_CHIL_02", "gradual_degradation"),
    ("IN_BLR_CHIL_03", "sudden_spike"),
    ("IN_BLR_CHIL_04", "healthy"),
    ("IN_BLR_CHIL_05", "sensor_dropout"),
    ("IN_BLR_CHIL_06", "out_of_order"),
    ("IN_BLR_CHIL_07", "malformed"),

    # Pump-motors
    ("IN_BLR_PUMO_01", "healthy"),
    ("IN_BLR_PUMO_02", "gradual_degradation"),
    ("IN_BLR_PUMO_03", "sudden_spike"),
    ("IN_BLR_PUMO_04", "duplicate"),
]

# ── Metric normal ranges ──────────────────────────────────────────────────────
# Used by scenarios to generate realistic baseline values
METRIC_RANGES = {
    "CHIL": {
        "coolant_pressure_psi": (100.0, 140.0),
        "flow_rate_l_min":      (38.0,  52.0),
        "ambient_temp_c":       (16.0,  22.0),
    },
    "PUMO": {
        "vibration_rms_mm_s":   (0.5,   3.0),
        "temperature_c":        (35.0,  55.0),
        "current_draw_amps":    (10.0,  15.0),
    },
}

# ── Degradation config ────────────────────────────────────────────────────────
# Which metric degrades per device type, and over how many ticks
DEGRADATION = {
    "CHIL": {
        "metric":     "coolant_pressure_psi",
        "direction":  "down",        # pressure drops
        "over_ticks": 6000,          # ~20 minutes at 200ms per tick
    },
    "PUMO": {
        "metric":     "temperature_c",
        "direction":  "up",          # temperature rises
        "over_ticks": 6000,
    },
}

# ── Spike config ──────────────────────────────────────────────────────────────
SPIKE = {
    "CHIL": {
        "metric":       "coolant_pressure_psi",
        "spike_value":  170.0,       # well above 140 threshold
        "duration_ticks": 30,
        "every_ticks":  300,         # spike every ~60 seconds
    },
    "PUMO": {
        "metric":       "vibration_rms_mm_s",
        "spike_value":  9.5,
        "duration_ticks": 30,
        "every_ticks":  300,
    },
}

# ── Sensor dropout config ─────────────────────────────────────────────────────
DROPOUT = {
    "silence_ticks_min": 300,    # minimum silence ~60 seconds
    "silence_ticks_max": 600,    # maximum silence ~120 seconds
}