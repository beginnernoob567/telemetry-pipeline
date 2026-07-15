# telemetry_device_consumer/config.py
import os

# ── Redpanda ──────────────────────────────────────────────────────────────────
REDPANDA_BROKERS   = os.getenv("REDPANDA_BROKERS", "localhost:9092")
CONSUMER_GROUP_ID  = os.getenv("CONSUMER_GROUP_ID", "telemetry-consumer-group")

TOPICS = [
    "telemetry/cooling-units",
    "telemetry/pump-motors",
]

# ── TimescaleDB ───────────────────────────────────────────────────────────────
POSTGRES_HOST     = os.getenv("POSTGRES_HOST",     "localhost")
POSTGRES_PORT     = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB       = os.getenv("POSTGRES_DB",       "telemetry")
POSTGRES_USER     = os.getenv("POSTGRES_USER",     "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")

DATABASE_URL = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

# ── Analysis ──────────────────────────────────────────────────────────────────
DISCONNECTION_THRESHOLD_S    = int(os.getenv("DISCONNECTION_THRESHOLD_S", "30"))
ALERT_COOLDOWN_S             = int(os.getenv("ALERT_COOLDOWN_S",          "300"))
ISOLATION_FOREST_MIN_READINGS = int(os.getenv("ISOLATION_FOREST_MIN_READINGS", "50"))
ISOLATION_FOREST_RETRAIN_EVERY = int(os.getenv("ISOLATION_FOREST_RETRAIN_EVERY", "100"))

# ── Consumer behaviour ────────────────────────────────────────────────────────
# How often the disconnection watchdog checks device last_seen (seconds)
WATCHDOG_INTERVAL_S = int(os.getenv("WATCHDOG_INTERVAL_S", "10"))