# telemetry_device_consumer/consumer.py
import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'common'))

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError
import asyncpg

from config import (
    REDPANDA_BROKERS, CONSUMER_GROUP_ID, TOPICS,
    DISCONNECTION_THRESHOLD_S, WATCHDOG_INTERVAL_S,
)
from validator import validate
from storage import (
    insert_telemetry,
    insert_failure,
    upsert_device_status,
    mark_devices_disconnected,
)
from analyser import Analyser
from alerting import handle_anomalies, resolve_alert

logger = logging.getLogger(__name__)


class TelemetryConsumer:
    def __init__(self, pool: asyncpg.Pool):
        self.pool     = pool
        self.analyser = Analyser()
        self._consumer: AIOKafkaConsumer | None = None

    async def start(self):
        self._consumer = AIOKafkaConsumer(
            *TOPICS,
            bootstrap_servers=REDPANDA_BROKERS,
            group_id=CONSUMER_GROUP_ID,
            enable_auto_commit=False,       # manual commit after write + analysis
            auto_offset_reset="earliest",
            value_deserializer=lambda v: v, # raw bytes — we decode in validator
        )
        await self._consumer.start()
        logger.info(
            "Consumer started | brokers=%s | topics=%s",
            REDPANDA_BROKERS, TOPICS,
        )

    async def stop(self):
        if self._consumer:
            await self._consumer.stop()
            logger.info("Consumer stopped")

    async def run(self):
        """Main consume loop — runs indefinitely."""
        async for message in self._consumer:
            await self._process(message)

    async def _process(self, message):
        """
        Full processing pipeline for a single Redpanda message.

        Order:
          1. Validate
          2. Write to telemetry (if valid)
          3. Update device_status last_seen
          4. Run anomaly detection
          5. Handle alerts
          6. Commit offset
        """
        raw = message.value

        # ── Step 1: Validate ──────────────────────────────────────────────────
        result = validate(raw)

        if not result.is_valid:
            logger.error(
                "Invalid message on topic=%s | reason=%s",
                message.topic, result.failure.error_reason,
            )
            await insert_failure(self.pool, result.failure)
            # commit even for bad messages — don't replay poison pills
            await self._consumer.commit()
            return

        reading = result.reading

        # ── Step 2: Write telemetry ───────────────────────────────────────────
        try:
            await insert_telemetry(self.pool, reading)
        except Exception as exc:
            logger.error(
                "Failed to write telemetry for %s: %s",
                reading.device_id, exc,
            )
            # do not commit — replay this message on restart
            return

        # ── Step 3: Update device last_seen ───────────────────────────────────
        await upsert_device_status(
            pool=self.pool,
            device_id=reading.device_id,
            device_type=reading.device_type,
            status="healthy",
            now=reading.timestamp,
        )

        # ── Step 4: Anomaly detection ─────────────────────────────────────────
        anomalies = self.analyser.analyse(
            device_id=reading.device_id,
            device_type=reading.device_type,
            timestamp=reading.timestamp,
            metrics=reading.metrics,
        )

        # ── Step 5: Handle alerts ─────────────────────────────────────────────
        if anomalies:
            await handle_anomalies(
                pool=self.pool,
                device_id=reading.device_id,
                device_type=reading.device_type,
                anomalies=anomalies,
            )
        else:
            # no anomalies — check if we can resolve existing alerts
            # for metrics that have returned to normal
            await self._check_resolutions(reading)

        # ── Step 6: Commit offset ─────────────────────────────────────────────
        # only reached if write + analysis both succeeded
        await self._consumer.commit()

    async def _check_resolutions(self, reading):
        """
        For each metric in a clean reading, check if there's
        an active alert for it that can now be resolved.
        """
        import asyncpg
        async with self.pool.acquire() as conn:
            active = await conn.fetch(
                """
                SELECT metric FROM current_alerts
                WHERE device_id = $1
                """,
                reading.device_id,
            )

        active_metrics = {r["metric"] for r in active}
        clean_metrics  = set(reading.metrics.keys())

        for metric in active_metrics & clean_metrics:
            await resolve_alert(
                pool=self.pool,
                device_id=reading.device_id,
                metric=metric,
            )


async def run_watchdog(pool: asyncpg.Pool):
    """
    Background task that periodically checks for devices
    that have gone silent and marks them disconnected.
    Runs every WATCHDOG_INTERVAL_S seconds.
    """
    logger.info(
        "Watchdog started — checking every %ds, "
        "threshold=%ds",
        WATCHDOG_INTERVAL_S,
        DISCONNECTION_THRESHOLD_S,
    )
    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL_S)
        try:
            await mark_devices_disconnected(
                pool=pool,
                disconnection_threshold_s=DISCONNECTION_THRESHOLD_S,
            )
        except Exception as exc:
            logger.error("Watchdog error: %s", exc)