# telemetry_device_sim/main.py
import asyncio
import logging
import signal
import sys

from config import FLEET
from producer import TelemetryProducer
from simulator import simulate_device

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


async def main():
    producer = TelemetryProducer()
    await producer.start()

    # graceful shutdown on SIGTERM / SIGINT (Docker sends SIGTERM on stop)
    loop = asyncio.get_running_loop()

    async def shutdown():
        logger.info("Shutdown signal received — stopping simulator")
        await producer.stop()
        sys.exit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig, lambda: asyncio.create_task(shutdown())
        )

    logger.info("Starting simulator — fleet size: %d devices", len(FLEET))

    # launch one coroutine per device, all run concurrently
    await asyncio.gather(*[
        simulate_device(device_id, scenario, producer)
        for device_id, scenario in FLEET
    ])


if __name__ == "__main__":
    asyncio.run(main())