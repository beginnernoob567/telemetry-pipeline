# streamlit_dashboard/queries.py
import json
import logging
import psycopg2
import psycopg2.extras
from config import (
    POSTGRES_HOST, POSTGRES_PORT,
    POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD,
)

logger = logging.getLogger(__name__)


def get_connection():
    return psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
    )


def fetch(query: str, params: tuple = ()) -> list[dict]:
    """Execute a read-only query and return rows as dicts."""
    try:
        with get_connection() as conn:
            with conn.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor
            ) as cur:
                cur.execute(query, params)
                return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.error("Query failed: %s | %s", exc, query[:100])
        return []


# ── Fleet overview ────────────────────────────────────────────────────────────

def get_fleet_status() -> list[dict]:
    return fetch(
        """
        SELECT
            device_id,
            device_type,
            status,
            since,
            last_seen
        FROM device_status
        ORDER BY
            CASE status
                WHEN 'unhealthy'    THEN 1
                WHEN 'disconnected' THEN 2
                ELSE 3
            END,
            device_id
        """
    )


def get_fleet_summary() -> dict:
    """Counts per status for the summary metric cards."""
    rows = fetch(
        """
        SELECT status, COUNT(*) as count
        FROM device_status
        GROUP BY status
        """
    )
    summary = {"healthy": 0, "unhealthy": 0, "disconnected": 0}
    for row in rows:
        summary[row["status"]] = row["count"]
    return summary


# ── Active alerts ─────────────────────────────────────────────────────────────

def get_current_alerts() -> list[dict]:
    return fetch(
        """
        SELECT
            device_id,
            metric,
            value,
            severity,
            detection_layer,
            message,
            first_seen,
            last_updated
        FROM current_alerts
        ORDER BY
            CASE severity
                WHEN 'CRITICAL' THEN 1
                WHEN 'WARNING'  THEN 2
                ELSE 3
            END,
            last_updated DESC
        """
    )


def get_alert_counts() -> dict:
    """Alert counts per severity for summary cards."""
    rows = fetch(
        """
        SELECT severity, COUNT(*) as count
        FROM current_alerts
        GROUP BY severity
        """
    )
    counts = {"CRITICAL": 0, "WARNING": 0, "INFO": 0}
    for row in rows:
        counts[row["severity"]] = row["count"]
    return counts


# ── Per-device ────────────────────────────────────────────────────────────────

def get_device_telemetry(
    device_id: str,
    minutes:   int = 30,
) -> list[dict]:
    """
    Returns recent telemetry for one device.
    Expands the metrics JSONB into flat rows for charting.
    """
    rows = fetch(
        """
        SELECT timestamp, metrics
        FROM telemetry
        WHERE
            device_id = %s
            AND timestamp > NOW() - (%s || ' minutes')::INTERVAL
        ORDER BY timestamp ASC
        """,
        (device_id, str(minutes)),
    )
    # expand JSONB metrics into flat structure
    expanded = []
    for row in rows:
        metrics = row["metrics"]
        if isinstance(metrics, str):
            metrics = json.loads(metrics)
        expanded.append({
            "timestamp": row["timestamp"],
            **metrics,
        })
    return expanded


def get_device_alert_history(
    device_id: str,
    limit:     int = 50,
) -> list[dict]:
    return fetch(
        """
        SELECT
            metric,
            value,
            severity,
            detection_layer,
            message,
            timestamp
        FROM alert_history
        WHERE device_id = %s
        ORDER BY timestamp DESC
        LIMIT %s
        """,
        (device_id, limit),
    )


def get_device_status(device_id: str) -> dict | None:
    rows = fetch(
        """
        SELECT device_id, device_type, status, since, last_seen
        FROM device_status
        WHERE device_id = %s
        """,
        (device_id,),
    )
    return rows[0] if rows else None


# ── Failures ──────────────────────────────────────────────────────────────────

def get_recent_failures(limit: int = 20) -> list[dict]:
    return fetch(
        """
        SELECT
            failure_id,
            raw_payload,
            error_reason,
            received_at,
            resolved
        FROM failures
        ORDER BY received_at DESC
        LIMIT %s
        """,
        (limit,),
    )


def get_failure_count() -> int:
    rows = fetch(
        "SELECT COUNT(*) as count FROM failures WHERE resolved = FALSE"
    )
    return rows[0]["count"] if rows else 0