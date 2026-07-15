# streamlit_dashboard/config.py
import os

POSTGRES_HOST     = os.getenv("POSTGRES_HOST",     "localhost")
POSTGRES_PORT     = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB       = os.getenv("POSTGRES_DB",       "telemetry")
POSTGRES_USER     = os.getenv("POSTGRES_USER",     "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")

DATABASE_URL = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

DASHBOARD_REFRESH_SECONDS = int(os.getenv("DASHBOARD_REFRESH_SECONDS", "10"))

# Severity colour mapping
SEVERITY_COLOURS = {
    "CRITICAL": "#e74c3c",
    "WARNING":  "#f39c12",
    "INFO":     "#3498db",
}

STATUS_COLOURS = {
    "healthy":      "#2ecc71",
    "unhealthy":    "#e74c3c",
    "disconnected": "#95a5a6",
}

STATUS_ICONS = {
    "healthy":      "✅",
    "unhealthy":    "🔴",
    "disconnected": "⚫",
}