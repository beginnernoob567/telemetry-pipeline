# telemetry_device_consumer/validator.py
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'common'))

from models import TelemetryMessage, EnrichedReading, FailureRecord
from device_utils import decode_message, enrich, validate_metrics, is_suspect_zero

logger = logging.getLogger(__name__)


class ValidationResult:
    """
    Wraps the outcome of validating a raw Redpanda message.
    Exactly one of `reading` or `failure` will be set.
    """
    def __init__(
        self,
        reading:       EnrichedReading | None = None,
        failure:       FailureRecord   | None = None,
        suspect_zeros: list[str]              = None,
        warnings:      list[str]              = None,
    ):
        self.reading       = reading
        self.failure       = failure
        self.suspect_zeros = suspect_zeros or []
        self.warnings      = warnings      or []

    @property
    def is_valid(self) -> bool:
        return self.reading is not None


def validate(raw: bytes) -> ValidationResult:
    """
    Full validation pipeline for a raw Redpanda message.

    Steps:
      1. JSON decode + Pydantic parse
      2. device_id format check (done inside TelemetryMessage)
      3. Metric set validation against known device type
      4. Suspect zero detection
      5. Enrich with device_id components

    Returns a ValidationResult — caller decides what to do
    with failures vs valid readings.
    """

    # ── Step 1: decode ────────────────────────────────────────────────────────
    message = decode_message(raw)
    if message is None:
        return ValidationResult(
            failure=FailureRecord(
                raw_payload=raw.decode("utf-8", errors="replace")[:2000],
                error_reason="JSON decode error or schema validation failure",
            )
        )

    # ── Step 2: device_id format already validated by Pydantic model ──────────
    # parse_device_id is called inside enrich() below

    # ── Step 3: metric set validation ─────────────────────────────────────────
    device_type = message.device_id.split("_")[2]
    warnings    = validate_metrics(device_type, message.metrics)

    if warnings:
        for w in warnings:
            logger.warning("[%s] %s", message.device_id, w)

    # missing ALL expected metrics is a hard failure
    from device_utils import DEVICE_METRICS
    expected = DEVICE_METRICS.get(device_type, set())
    present  = set(message.metrics.keys()) & expected

    if not present:
        return ValidationResult(
            failure=FailureRecord(
                raw_payload=str(message.model_dump()),
                error_reason=(
                    f"No recognised metrics for device_type={device_type}. "
                    f"Expected one of: {expected}"
                ),
            )
        )

    # ── Step 4: suspect zero detection ────────────────────────────────────────
    suspect_zeros = is_suspect_zero(message.metrics)
    if suspect_zeros:
        logger.info(
            "[%s] Suspect zero readings on: %s",
            message.device_id, suspect_zeros,
        )

    # ── Step 5: enrich ────────────────────────────────────────────────────────
    try:
        reading = enrich(message)
    except ValueError as exc:
        return ValidationResult(
            failure=FailureRecord(
                raw_payload=str(message.model_dump()),
                error_reason=str(exc),
            )
        )

    return ValidationResult(
        reading=reading,
        suspect_zeros=suspect_zeros,
        warnings=warnings,
    )