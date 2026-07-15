# telemetry_device_sim/simulator.py
import asyncio
import logging
import random
import time
from datetime import datetime, timezone

from config import BASE_TICK_MS, JITTER_MS
from producer import TelemetryProducer
from scenarios import ScenarioState

logger = logging.getLogger(__name__)

def _build_payload(
    device_id: str,
    metrics: dict,
    scenario: str,
) -> dict:
    """Build the full telemetry payload from raw metrics."""
    offset_s   = metrics.pop("_timestamp_offset_s", 0)
    is_dup     = metrics.pop("_duplicate", False)

    ts = datetime.now(timezone.utc)
    if offset_s:
        from datetime import timedelta
        ts = ts + timedelta(seconds=offset_s)

    payload = {
        "device_id":   device_id,
        "timestamp":   ts.isoformat(),
        "metrics":     metrics,
        "scenario":    scenario,
        "_duplicate":  is_dup,   # consumed by producer, not stored in DB
    }
    return payload


async def simulate_device(
    device_id: str,
    scenario: str,
    producer: TelemetryProducer,
):
    """
    Coroutine that runs indefinitely for a single device.
    Each tick emits one reading (or nothing, for dropout scenario).
    Tick interval = BASE_TICK_MS ± JITTER_MS — unique per device
    so all devices are unsynchronised.
    """
    device_type  = device_id.split("_")[2]
    tick_ms      = BASE_TICK_MS + random.randint(-JITTER_MS, JITTER_MS)
    tick_s       = tick_ms / 1000

    state = ScenarioState(device_id, scenario)

    logger.info(
        "Starting device %s | scenario=%s | tick=%.0fms",
        device_id, scenario, tick_ms,
    )

    while True:
        try:
            metrics = state.next()

            if metrics is None:
                # dropout scenario — device is silent this tick
                logger.debug("%s is in dropout window, skipping tick", device_id)
            else:
                payload   = _build_payload(device_id, dict(metrics), scenario)
                is_dup    = payload.pop("_duplicate", False)

                await producer.send(device_id, device_type, payload)
                logger.debug("Emitted %s | tick=%d", device_id, state.tick)

                if is_dup:
                    # send the identical payload a second time
                    await producer.send(device_id, device_type, payload)
                    logger.debug("Duplicate emitted for %s", device_id)

        except Exception as exc:
            logger.error("Unhandled error in device loop %s: %s", device_id, exc)

        await asyncio.sleep(tick_s)