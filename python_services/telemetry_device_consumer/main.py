# telemetry_device_consumer/main.py
import asyncio
import logging
import signal
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'common'))

from storage import create_pool
from consumer import TelemetryConsumer, run_watchdog

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


async def main():
    # ── DB connection pool ────────────────────────────────────────────────────
    pool = await create_pool()

    # ── Consumer ──────────────────────────────────────────────────────────────
    consumer = TelemetryConsumer(pool)
    await consumer.start()

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    async def shutdown():
        logger.info("Shutdown signal received")
        shutdown_event.set()
        await consumer.stop()
        await pool.close()
        logger.info("Consumer shut down cleanly")

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig, lambda: asyncio.create_task(shutdown())
        )

    logger.info("Consumer service started")

    # ── Run consumer + watchdog concurrently ──────────────────────────────────
    try:
        await asyncio.gather(
            consumer.run(),
            run_watchdog(pool),
        )
    except asyncio.CancelledError:
        pass
    finally:
        await consumer.stop()
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())