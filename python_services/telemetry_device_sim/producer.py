# telemetry_device_sim/producer.py
import json
import logging
from aiokafka import AIOKafkaProducer
from config import REDPANDA_BROKERS, TOPICS

logger = logging.getLogger(__name__)


class TelemetryProducer:
    def __init__(self):
        self._producer: AIOKafkaProducer | None = None

    async def start(self):
        self._producer = AIOKafkaProducer(
            bootstrap_servers=REDPANDA_BROKERS,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
            # ensure messages are durable before ack
            acks="all",
            # retry on transient broker errors
            retry_backoff_ms=200,
            request_timeout_ms=10_000,
        )
        await self._producer.start()
        logger.info("Producer connected to Redpanda at %s", REDPANDA_BROKERS)

    async def stop(self):
        if self._producer:
            await self._producer.stop()

    async def send(self, device_id: str, device_type: str, payload: dict):
        topic = TOPICS.get(device_type)
        if not topic:
            logger.error("No topic mapped for device_type=%s", device_type)
            return

        try:
            logger.info("Publishing to %s", topic)
            await self._producer.send_and_wait(
                topic,
                key=device_id,       # partition by device_id
                value=payload,
            )
        except Exception as exc:
            logger.error(
                "Failed to produce message for %s: %s", device_id, exc
            )
            # route to failures topic so nothing is silently lost
            await self._producer.send_and_wait(
                TOPICS["failures"],
                key=device_id,
                value={
                    "raw":    payload,
                    "reason": str(exc),
                    "source": "simulator",
                },
            )