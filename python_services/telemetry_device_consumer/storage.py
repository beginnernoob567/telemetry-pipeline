# telemetry_device_consumer/storage.py
import logging
import asyncpg
from datetime import datetime, timezone

from models import EnrichedReading, AlertRecord, FailureRecord
from config import (
    POSTGRES_HOST, POSTGRES_PORT,
    POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD,
)

logger = logging.getLogger(__name__)


async def create_pool() -> asyncpg.Pool:
    """Create and return an asyncpg connection pool."""
    pool = await asyncpg.create_pool(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        database=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        min_size=2,
        max_size=10,
    )
    logger.info(
        "Connected to TimescaleDB at %s:%s/%s",
        POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB,
    )
    return pool


# ── Telemetry ─────────────────────────────────────────────────────────────────

async def insert_telemetry(
    pool: asyncpg.Pool,
    reading: EnrichedReading,
) -> None:
    """Insert a single enriched reading into the telemetry hypertable."""
    import json
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO telemetry
                (timestamp, device_id, country, city,
                 device_type, unit_id, metrics, scenario)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            reading.timestamp,
            reading.device_id,
            reading.country,
            reading.city,
            reading.device_type,
            reading.unit_id,
            json.dumps(reading.metrics),
            reading.scenario,
        )


# ── Device status ─────────────────────────────────────────────────────────────

async def upsert_device_status(
    pool:        asyncpg.Pool,
    device_id:   str,
    device_type: str,
    status:      str,            # healthy / unhealthy / disconnected
    now:         datetime | None = None,
) -> None:
    """
    Insert or update the device_status row for this device.
    `since` is only updated when the status actually changes.
    `last_seen` is always updated on every healthy reading.
    """
    now = now or datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO device_status
                (device_id, device_type, status, since, last_seen)
            VALUES ($1, $2, $3, $4, $4)
            ON CONFLICT (device_id) DO UPDATE SET
                last_seen = EXCLUDED.last_seen,
                -- only update status and since when status actually changes
                since = CASE
                    WHEN device_status.status != EXCLUDED.status
                    THEN EXCLUDED.since
                    ELSE device_status.since
                END,
                status = EXCLUDED.status,
                device_type = EXCLUDED.device_type
            """,
            device_id,
            device_type,
            status,
            now,
        )


async def mark_devices_disconnected(
    pool:                   asyncpg.Pool,
    disconnection_threshold_s: int,
) -> list[str]:
    """
    Mark any device whose last_seen is older than the threshold
    as disconnected. Returns list of newly disconnected device_ids.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            UPDATE device_status
            SET
                status = 'disconnected',
                since  = NOW()
            WHERE
                last_seen < NOW() - ($1 || ' seconds')::INTERVAL
                AND status != 'disconnected'
            RETURNING device_id
            """,
            str(disconnection_threshold_s),
        )
    disconnected = [r["device_id"] for r in rows]
    if disconnected:
        logger.warning("Devices marked disconnected: %s", disconnected)
    return disconnected


# ── Alerts ────────────────────────────────────────────────────────────────────

async def upsert_current_alert(
    pool:  asyncpg.Pool,
    alert: AlertRecord,
) -> None:
    """
    Upsert into current_alerts — one row per (device_id, metric) pair.
    Updates last_updated on conflict, preserves first_seen.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO current_alerts
                (device_id, metric, value, severity,
                 detection_layer, message, first_seen, last_updated)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $7)
            ON CONFLICT (device_id, metric) DO UPDATE SET
                value           = EXCLUDED.value,
                severity        = EXCLUDED.severity,
                detection_layer = EXCLUDED.detection_layer,
                message         = EXCLUDED.message,
                last_updated    = EXCLUDED.last_updated
            """,
            alert.device_id,
            alert.metric,
            alert.value,
            alert.severity,
            alert.detection_layer,
            alert.message,
            alert.timestamp,
        )


async def insert_alert_history(
    pool:  asyncpg.Pool,
    alert: AlertRecord,
) -> None:
    """Append to the alert_history audit log — never updated."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO alert_history
                (device_id, device_type, metric, value, timestamp,
                 severity, detection_layer, message)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            alert.device_id,
            alert.device_type,
            alert.metric,
            alert.value,
            alert.timestamp,
            alert.severity,
            alert.detection_layer,
            alert.message,
        )


async def get_recent_alert(
    pool:      asyncpg.Pool,
    device_id: str,
    metric:    str,
    within_s:  int,
) -> bool:
    """
    Returns True if an alert for this device+metric pair
    was inserted within the last `within_s` seconds.
    Used for cooldown enforcement.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT 1 FROM alert_history
            WHERE
                device_id = $1
                AND metric = $2
                AND timestamp > NOW() - ($3 || ' seconds')::INTERVAL
            LIMIT 1
            """,
            device_id,
            metric,
            str(within_s),
        )
    return row is not None


# ── Failures ──────────────────────────────────────────────────────────────────

async def insert_failure(
    pool:    asyncpg.Pool,
    failure: FailureRecord,
) -> None:
    """Write an unprocessable message to the failures table."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO failures (raw_payload, error_reason)
            VALUES ($1, $2)
            """,
            failure.raw_payload,
            failure.error_reason,
        )


# ── Dashboard queries ─────────────────────────────────────────────────────────

async def get_fleet_status(pool: asyncpg.Pool) -> list[dict]:
    """All devices and their current status — for fleet overview panel."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                device_id, device_type, status,
                since, last_seen
            FROM device_status
            ORDER BY status DESC, device_id
            """
        )
    return [dict(r) for r in rows]


async def get_current_alerts(pool: asyncpg.Pool) -> list[dict]:
    """All active alerts — for the alert panel."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                device_id, metric, value, severity,
                detection_layer, message,
                first_seen, last_updated
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
    return [dict(r) for r in rows]


async def get_device_history(
    pool:      asyncpg.Pool,
    device_id: str,
    minutes:   int = 30,
) -> list[dict]:
    """Recent telemetry for a single device — for per-device charts."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT timestamp, metrics
            FROM telemetry
            WHERE
                device_id = $1
                AND timestamp > NOW() - ($2 || ' minutes')::INTERVAL
            ORDER BY timestamp ASC
            """,
            device_id,
            str(minutes),
        )
    return [dict(r) for r in rows]


async def get_alert_history(
    pool:      asyncpg.Pool,
    device_id: str | None = None,
    limit:     int        = 50,
) -> list[dict]:
    """Recent alert history, optionally filtered by device."""
    async with pool.acquire() as conn:
        if device_id:
            rows = await conn.fetch(
                """
                SELECT
                    device_id, metric, value, severity,
                    detection_layer, message, timestamp
                FROM alert_history
                WHERE device_id = $1
                ORDER BY timestamp DESC
                LIMIT $2
                """,
                device_id, limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT
                    device_id, metric, value, severity,
                    detection_layer, message, timestamp
                FROM alert_history
                ORDER BY timestamp DESC
                LIMIT $1
                """,
                limit,
            )
    return [dict(r) for r in rows]