# common/models.py
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, field_validator


class TelemetryMessage(BaseModel):
    """
    Represents a raw message received from Redpanda.
    Minimal payload — consumer derives country/city/device_type/unit_id
    from device_id.
    """
    device_id:  str
    timestamp:  datetime
    metrics:    dict[str, float]
    scenario:   Optional[str] = None

    @field_validator("device_id")
    @classmethod
    def validate_device_id(cls, v: str) -> str:
        parts = v.split("_")
        if len(parts) != 4:
            raise ValueError(
                f"device_id must follow <COUNTRY>_<CITY>_<TYPE>_<ID> "
                f"format, got: {v}"
            )
        return v

    @field_validator("metrics")
    @classmethod
    def validate_metrics_not_empty(cls, v: dict) -> dict:
        if not v:
            raise ValueError("metrics dict cannot be empty")
        return v


class EnrichedReading(BaseModel):
    """
    TelemetryMessage enriched with fields derived from device_id.
    This is what gets written to TimescaleDB.
    """
    device_id:   str
    country:     str
    city:        str
    device_type: str
    unit_id:     str
    timestamp:   datetime
    metrics:     dict[str, float]
    scenario:    Optional[str] = None


class AlertRecord(BaseModel):
    """
    Represents an anomaly detected by the analyser.
    Written to both alert_history and current_alerts.
    """
    device_id:       str
    device_type:     str
    metric:          str
    value:           float
    timestamp:       datetime
    severity:        str
    detection_layer: str
    message:         str


class FailureRecord(BaseModel):
    """
    Represents an unprocessable message routed to the failures table.
    """
    raw_payload:  str
    error_reason: str