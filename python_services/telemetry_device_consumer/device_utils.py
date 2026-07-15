#device_utils.py
import json
import logging
from datetime import datetime, timezone

from models import TelemetryMessage, EnrichedReading

logger = logging.getLogger(__name__)

# ── Valid device types and their expected metrics ─────────────────────────────
DEVICE_METRICS = {
    "CHIL": {
        "coolant_pressure_psi",
        "flow_rate_l_min",
        "ambient_temp_c",
    },
    "PUMO": {
        "vibration_rms_mm_s",
        "temperature_c",
        "current_draw_amps",
    },
}

VALID_DEVICE_TYPES = set(DEVICE_METRICS.keys())


def parse_device_id(device_id: str) -> dict:
    """
    Splits IN_BLR_CHIL_01 into its components.
    Returns a dict with country, city, device_type, unit_id.
    Raises ValueError if the format is invalid.
    """
    parts = device_id.split("_")
    if len(parts) != 4:
        raise ValueError(
            f"Invalid device_id format: {device_id}. "
            f"Expected <COUNTRY>_<CITY>_<TYPE>_<UNIT>"
        )

    country, city, device_type, unit_id = parts

    if device_type not in VALID_DEVICE_TYPES:
        raise ValueError(
            f"Unknown device_type '{device_type}' in device_id '{device_id}'. "
            f"Valid types: {VALID_DEVICE_TYPES}"
        )

    return {
        "country":     country,
        "city":        city,
        "device_type": device_type,
        "unit_id":     unit_id,
    }


def enrich(message: TelemetryMessage) -> EnrichedReading:
    """
    Takes a validated TelemetryMessage and returns an EnrichedReading
    with device_id components extracted as separate fields.
    """
    parts = parse_device_id(message.device_id)

    return EnrichedReading(
        device_id=message.device_id,
        country=parts["country"],
        city=parts["city"],
        device_type=parts["device_type"],
        unit_id=parts["unit_id"],
        timestamp=message.timestamp,
        metrics=message.metrics,
        scenario=message.scenario,
    )


def validate_metrics(device_type: str, metrics: dict) -> list[str]:
    """
    Checks that the metrics in the payload match what's expected
    for the device type. Returns a list of warning strings —
    empty means all metrics are valid.
    """
    warnings = []
    expected = DEVICE_METRICS.get(device_type, set())

    unknown = set(metrics.keys()) - expected
    if unknown:
        warnings.append(
            f"Unexpected metrics for {device_type}: {unknown}"
        )

    missing = expected - set(metrics.keys())
    if missing:
        warnings.append(
            f"Missing expected metrics for {device_type}: {missing}"
        )

    return warnings


def decode_message(raw: bytes) -> TelemetryMessage | None:
    """
    Decodes a raw Redpanda message into a TelemetryMessage.
    Returns None if the message is corrupt or invalid —
    caller is responsible for routing to failures.
    """
    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.error("JSON decode error: %s | raw=%s", exc, raw[:200])
        return None

    try:
        return TelemetryMessage(**data)
    except Exception as exc:
        logger.error("Validation error: %s | data=%s", exc, data)
        return None


def is_suspect_zero(metrics: dict) -> list[str]:
    """
    Returns a list of metric names that reported zero.
    A zero reading may indicate a faulty sensor rather than
    broken equipment — flagged as INFO not CRITICAL.
    """
    return [m for m, v in metrics.items() if v == 0.0]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)