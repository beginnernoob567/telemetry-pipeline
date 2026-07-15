# telemetry_device_sim/scenarios.py
import random
import time
from config import METRIC_RANGES, DEGRADATION, SPIKE, DROPOUT
from datetime import datetime, timezone

def _device_type(device_id: str) -> str:
    return device_id.split("_")[2]  # IN_BLR_CHIL_01 → CHIL


def _baseline(device_type: str) -> dict:
    """Generate a clean reading within normal range with small Gaussian noise."""
    metrics = {}
    for metric, (low, high) in METRIC_RANGES[device_type].items():
        mid   = (low + high) / 2
        sigma = (high - low) / 8     # keeps ~99% of values inside the range
        value = random.gauss(mid, sigma)
        value = round(max(low * 0.95, min(high * 1.05, value)), 2)
        metrics[metric] = value
    return metrics

def _malformed_baseline(device_type: str) -> dict:
    """Generate a malformed payload — rotates through different failure types."""
    kind = random.randint(0, 2)
    if kind == 0:
        # missing metrics key entirely
        return {
            "_malformed": {
                "device_id": f"IN_BLR_{device_type}_07",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        }
    elif kind == 1:
        # device_id fails format check
        return {
            "_malformed": {
                "device_id": "BADFORMAT",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "metrics":   _baseline(device_type),
            }
        }
    else:
        # not valid JSON structure
        return {"_malformed": "__NOTJSON__"}


class ScenarioState:
    """
    Holds per-device mutable state so each device's scenario
    progresses independently across ticks.
    """
    def __init__(self, device_id: str, scenario: str):
        self.device_id   = device_id
        self.scenario    = scenario
        self.device_type = _device_type(device_id)
        self.tick        = 0

        # gradual degradation
        self.deg_cfg     = DEGRADATION.get(self.device_type, {})

        # sudden spike
        self.spk_cfg     = SPIKE.get(self.device_type, {})
        self.spike_ticks_remaining = 0

        # sensor dropout
        self.dro_cfg        = DROPOUT
        self.dropout_remaining = 0
        self.next_dropout_at = random.randint(
            self.dro_cfg["silence_ticks_min"],
            self.dro_cfg["silence_ticks_max"],
        )

    def next(self) -> dict | None:
        """
        Returns the next metrics dict for this device, or None
        if the device is in a dropout window (no message emitted).
        """
        self.tick += 1
        handler = getattr(self, f"_scenario_{self.scenario}")
        return handler()

    # ── scenario handlers ─────────────────────────────────────────────────────

    def _scenario_healthy(self) -> dict:
        return _baseline(self.device_type)

    def _scenario_gradual_degradation(self) -> dict:
        metrics = _baseline(self.device_type)
        metric    = self.deg_cfg["metric"]
        direction = self.deg_cfg["direction"]
        over      = self.deg_cfg["over_ticks"]

        low, high  = METRIC_RANGES[self.device_type][metric]
        progress   = min(self.tick / over, 1.0)   # 0.0 → 1.0 over degradation window

        if direction == "down":
            # drift from midpoint down to 50% below low threshold
            mid     = (low + high) / 2
            target  = low * 0.5
            metrics[metric] = round(mid - (mid - target) * progress, 2)
        else:
            # drift from midpoint up to 50% above high threshold
            mid     = (low + high) / 2
            target  = high * 1.5
            metrics[metric] = round(mid + (target - mid) * progress, 2)

        return metrics

    def _scenario_sudden_spike(self) -> dict:
        metrics = _baseline(self.device_type)
        metric  = self.spk_cfg["metric"]

        if self.spike_ticks_remaining > 0:
            metrics[metric] = self.spk_cfg["spike_value"]
            self.spike_ticks_remaining -= 1
        elif self.tick % self.spk_cfg["every_ticks"] == 0:
            metrics[metric] = self.spk_cfg["spike_value"]
            self.spike_ticks_remaining = self.spk_cfg["duration_ticks"] - 1

        return metrics

    def _scenario_sensor_dropout(self) -> dict | None:
        if self.dropout_remaining > 0:
            self.dropout_remaining -= 1
            return None     # no message emitted this tick

        if self.tick >= self.next_dropout_at:
            self.dropout_remaining = random.randint(
                self.dro_cfg["silence_ticks_min"],
                self.dro_cfg["silence_ticks_max"],
            )
            self.next_dropout_at = self.tick + random.randint(
                self.dro_cfg["silence_ticks_min"] * 3,
                self.dro_cfg["silence_ticks_max"] * 3,
            )
            return None

        return _baseline(self.device_type)

    def _scenario_out_of_order(self) -> dict:
        """Emits with a timestamp 2–8 seconds in the past."""
        metrics = _baseline(self.device_type)
        metrics["_timestamp_offset_s"] = -random.randint(2, 8)
        return metrics

    def _scenario_duplicate(self) -> dict:
        """Flags the reading so the producer sends it twice."""
        metrics = _baseline(self.device_type)
        metrics["_duplicate"] = True
        return metrics

    def _scenario_malformed(self) -> dict:
        return _malformed_baseline(self.device_type)
