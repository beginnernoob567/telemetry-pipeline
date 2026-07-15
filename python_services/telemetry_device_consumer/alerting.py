# telemetry_device_consumer/alerting.py
import logging
from datetime import datetime, timezone

import asyncpg

from analyser import AnomalyResult
from models import AlertRecord
from storage import (
    upsert_current_alert,
    insert_alert_history,
    get_recent_alert,
    upsert_device_status,
)
from config import ALERT_COOLDOWN_S

logger = logging.getLogger(__name__)


async def handle_anomalies(
    pool:        asyncpg.Pool,
    device_id:   str,
    device_type: str,
    anomalies:   list[AnomalyResult],
) -> int:
    """
    Processes a list of AnomalyResult objects from the analyser.

    For each anomaly:
      1. Check cooldown — skip if same device+metric alerted recently
      2. Upsert current_alerts — latest state per device+metric
      3. Append to alert_history — audit log
      4. Update device_status to unhealthy

    Returns the number of alerts actually written.
    """
    if not anomalies:
        return 0

    written = 0

    for anomaly in anomalies:
        # ── Cooldown check ────────────────────────────────────────────────────
        already_alerted = await get_recent_alert(
            pool=pool,
            device_id=device_id,
            metric=anomaly.metric,
            within_s=ALERT_COOLDOWN_S,
        )

        if already_alerted:
            logger.debug(
                "[%s] Cooldown active for metric=%s — suppressing alert",
                device_id, anomaly.metric,
            )
            continue

        # ── Build alert record ────────────────────────────────────────────────
        alert = AlertRecord(
            device_id=device_id,
            device_type=device_type,
            metric=anomaly.metric,
            value=anomaly.value,
            timestamp=anomaly.timestamp,
            severity=anomaly.severity,
            detection_layer=anomaly.detection_layer,
            message=anomaly.message,
        )

        # ── Write to DB ───────────────────────────────────────────────────────
        await upsert_current_alert(pool, alert)
        await insert_alert_history(pool, alert)

        # ── Mark device unhealthy ─────────────────────────────────────────────
        await upsert_device_status(
            pool=pool,
            device_id=device_id,
            device_type=device_type,
            status="unhealthy",
            now=anomaly.timestamp,
        )

        logger.warning(
            "[%s] ALERT | severity=%s | layer=%s | metric=%s | %s",
            device_id,
            anomaly.severity,
            anomaly.detection_layer,
            anomaly.metric,
            anomaly.message,
        )

        written += 1

    return written


async def resolve_alert(
    pool:      asyncpg.Pool,
    device_id: str,
    metric:    str,
) -> None:
    """
    Remove a metric from current_alerts when readings
    return to normal. Marks device healthy if no active
    alerts remain for it.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM current_alerts
            WHERE device_id = $1 AND metric = $2
            """,
            device_id, metric,
        )

        # check if any alerts remain for this device
        remaining = await conn.fetchval(
            """
            SELECT COUNT(*) FROM current_alerts
            WHERE device_id = $1
            """,
            device_id,
        )

    if remaining == 0:
        await upsert_device_status(
            pool=pool,
            device_id=device_id,
            device_type=device_id.split("_")[2],
            status="healthy",
        )
        logger.info(
            "[%s] All alerts resolved — device marked healthy", device_id
        )