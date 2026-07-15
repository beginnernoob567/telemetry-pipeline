# telemetry_device_consumer/analyser.py

import logging
import numpy as np
from scipy import stats
from sklearn.ensemble import IsolationForest
from dataclasses import dataclass, field
from collections import defaultdict
from datetime import datetime, timezone

from config import ISOLATION_FOREST_MIN_READINGS, ISOLATION_FOREST_RETRAIN_EVERY

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────
# (min_safe, max_safe) per metric
# Readings outside these ranges trigger Layer 1 immediately

THRESHOLDS = {
    # Chiller metrics
    "coolant_pressure_psi": (90.0,  150.0),
    "flow_rate_l_min":      (35.0,  55.0),
    "ambient_temp_c":       (14.0,  24.0),

    # Pump-motor metrics
    "vibration_rms_mm_s":   (0.0,   4.0),
    "temperature_c":        (30.0,  80.0),
    "current_draw_amps":    (8.0,   17.0),
}

# ── Z-score config ────────────────────────────────────────────────────────────
ZSCORE_WINDOW        = 30     # rolling window size in readings
ZSCORE_THRESHOLD     = 2.5    # z-score above this is anomalous
ZSCORE_MIN_READINGS  = 10     # minimum readings before z-score is meaningful
CONSECUTIVE_REQUIRED = 2      # consecutive anomalous readings before alert fires

# ── Isolation Forest config ───────────────────────────────────────────────────
IF_MIN_READINGS      = ISOLATION_FOREST_MIN_READINGS   # from config / .env
IF_RETRAIN_EVERY     = ISOLATION_FOREST_RETRAIN_EVERY  # from config / .env
IF_CONTAMINATION     = 0.05   # expected proportion of anomalies in training data


# ── Alert dataclass ───────────────────────────────────────────────────────────
@dataclass
class AnomalyResult:
    device_id:       str
    metric:          str
    value:           float
    severity:        str          # INFO / WARNING / CRITICAL
    detection_layer: str          # threshold / rolling_zscore / isolation_forest
    message:         str
    timestamp:       datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


# ── Per-device state ──────────────────────────────────────────────────────────
class DeviceState:
    """
    Holds all mutable per-device state needed for analysis.
    One instance per device_id, lives in memory for the lifetime
    of the consumer process.
    """

    def __init__(self, device_id: str, device_type: str):
        self.device_id   = device_id
        self.device_type = device_type

        # rolling history per metric: {metric: [float, ...]}
        self.history: dict[str, list[float]] = defaultdict(list)

        # consecutive anomaly counter per metric (Layer 2 spike suppression)
        self.consecutive_anomalies: dict[str, int] = defaultdict(int)

        # Isolation Forest models per device_type (shared across metrics)
        self.if_model: IsolationForest | None = None
        self.if_reading_count  = 0     # readings since last retrain
        self.if_total_readings = 0     # total readings seen

    def add_reading(self, metrics: dict):
        """Append each metric value to its rolling history window."""
        for metric, value in metrics.items():
            self.history[metric].append(float(value))
            # keep only the last ZSCORE_WINDOW readings
            if len(self.history[metric]) > ZSCORE_WINDOW:
                self.history[metric].pop(0)

        self.if_reading_count  += 1
        self.if_total_readings += 1

    def should_retrain_if(self) -> bool:
        return (
            self.if_total_readings >= IF_MIN_READINGS
            and self.if_reading_count >= IF_RETRAIN_EVERY
        )

    def enough_for_zscore(self, metric: str) -> bool:
        return len(self.history[metric]) >= ZSCORE_MIN_READINGS

    def enough_for_if(self) -> bool:
        return self.if_total_readings >= IF_MIN_READINGS


# ── Analyser ──────────────────────────────────────────────────────────────────
class Analyser:
    """
    Runs three detection layers against every incoming reading.
    Maintains per-device state in memory.
    """

    def __init__(self):
        # keyed by device_id
        self._states: dict[str, DeviceState] = {}

    def _get_state(self, device_id: str, device_type: str) -> DeviceState:
        if device_id not in self._states:
            self._states[device_id] = DeviceState(device_id, device_type)
        return self._states[device_id]

    # ── Public entry point ────────────────────────────────────────────────────

    def analyse(
        self,
        device_id:   str,
        device_type: str,
        timestamp:   datetime,
        metrics:     dict,
    ) -> list[AnomalyResult]:
        """
        Run all three detection layers against a single reading.
        Returns a list of AnomalyResult — empty if everything is normal.
        """
        state = self._get_state(device_id, device_type)

        # update rolling history before analysis so Layer 2 includes
        # the current reading in its window
        state.add_reading(metrics)

        results: list[AnomalyResult] = []

        for metric, value in metrics.items():
            value = float(value)

            # ── Layer 1: hardcoded thresholds ─────────────────────────────────
            layer1 = self._check_threshold(
                device_id, metric, value, timestamp
            )
            if layer1:
                results.append(layer1)
                # if threshold already fires, skip layers 2 and 3
                # for this metric — don't double-alert
                state.consecutive_anomalies[metric] = 0
                continue

            # ── Layer 2: rolling z-score ──────────────────────────────────────
            if state.enough_for_zscore(metric):
                layer2 = self._check_zscore(
                    state, device_id, metric, value, timestamp
                )
                if layer2:
                    results.append(layer2)
            else:
                # reset consecutive counter — not enough history yet
                state.consecutive_anomalies[metric] = 0

        # ── Layer 3: Isolation Forest (multivariate, per device) ──────────────
        if state.enough_for_if():
            if state.should_retrain_if():
                self._train_isolation_forest(state)

            layer3 = self._check_isolation_forest(
                state, device_id, timestamp, metrics
            )
            if layer3:
                results.append(layer3)

        return results

    # ── Layer 1: Threshold ────────────────────────────────────────────────────

    def _check_threshold(
        self,
        device_id: str,
        metric:    str,
        value:     float,
        timestamp: datetime,
    ) -> AnomalyResult | None:

        if metric not in THRESHOLDS:
            return None

        low, high = THRESHOLDS[metric]

        # zero reading — suspect sensor, not necessarily broken equipment
        if value == 0.0:
            return AnomalyResult(
                device_id=device_id,
                metric=metric,
                value=value,
                severity="INFO",
                detection_layer="threshold",
                message=(
                    f"{metric} on {device_id} reported zero — "
                    f"possible sensor fault rather than equipment failure. "
                    f"Flagged for investigation."
                ),
                timestamp=timestamp,
            )

        if value < low:
            pct_below = round((low - value) / low * 100, 1)
            severity  = "CRITICAL" if pct_below > 20 else "WARNING"
            return AnomalyResult(
                device_id=device_id,
                metric=metric,
                value=value,
                severity=severity,
                detection_layer="threshold",
                message=(
                    f"{metric} on {device_id} has dropped to {value} "
                    f"({pct_below}% below safe minimum of {low}). "
                    f"Immediate inspection recommended."
                    if severity == "CRITICAL" else
                    f"{metric} on {device_id} is below safe range "
                    f"({value}, minimum {low}). Monitor closely."
                ),
                timestamp=timestamp,
            )

        if value > high:
            pct_above = round((value - high) / high * 100, 1)
            severity  = "CRITICAL" if pct_above > 20 else "WARNING"
            return AnomalyResult(
                device_id=device_id,
                metric=metric,
                value=value,
                severity=severity,
                detection_layer="threshold",
                message=(
                    f"{metric} on {device_id} has exceeded critical limit "
                    f"({value}, {pct_above}% above maximum of {high}). "
                    f"Immediate inspection recommended."
                    if severity == "CRITICAL" else
                    f"{metric} on {device_id} is above safe range "
                    f"({value}, maximum {high}). Monitor closely."
                ),
                timestamp=timestamp,
            )

        return None

    # ── Layer 2: Rolling z-score ──────────────────────────────────────────────

    def _check_zscore(
        self,
        state:     DeviceState,
        device_id: str,
        metric:    str,
        value:     float,
        timestamp: datetime,
    ) -> AnomalyResult | None:

        window = state.history[metric]

        # exclude the current reading from the baseline
        # so we measure deviation against prior behaviour
        baseline = window[:-1]
        if len(baseline) < ZSCORE_MIN_READINGS:
            return None

        mean   = np.mean(baseline)
        std    = np.std(baseline)

        if std < 1e-6:
            # flat signal — no meaningful z-score
            return None

        z = abs((value - mean) / std)

        if z > ZSCORE_THRESHOLD:
            state.consecutive_anomalies[metric] += 1
        else:
            # reset — spike suppression requires consecutive readings
            state.consecutive_anomalies[metric] = 0
            return None

        if state.consecutive_anomalies[metric] < CONSECUTIVE_REQUIRED:
            # first anomalous reading — could be noise, wait for next
            logger.debug(
                "%s | %s | z=%.2f — waiting for consecutive confirmation",
                device_id, metric, z,
            )
            return None

        # confirmed — consecutive anomalous readings
        direction = "above" if value > mean else "below"
        return AnomalyResult(
            device_id=device_id,
            metric=metric,
            value=value,
            severity="WARNING",
            detection_layer="rolling_zscore",
            message=(
                f"{metric} on {device_id} is statistically anomalous "
                f"(z-score {z:.2f}, {CONSECUTIVE_REQUIRED} consecutive readings "
                f"{direction} baseline mean of {mean:.2f}). "
                f"Possible gradual drift or sustained deviation."
            ),
            timestamp=timestamp,
        )

    # ── Layer 3: Isolation Forest ─────────────────────────────────────────────

    def _train_isolation_forest(self, state: DeviceState):
        """
        Train (or retrain) the Isolation Forest model for this device
        using all metric histories currently in memory.
        """
        # build feature matrix — rows are readings, columns are metrics
        # use only metrics that have enough history
        metric_keys = sorted(state.history.keys())
        min_len     = min(len(state.history[m]) for m in metric_keys)

        if min_len < IF_MIN_READINGS:
            return

        X = np.column_stack([
            state.history[m][-min_len:] for m in metric_keys
        ])

        state.if_model = IsolationForest(
            contamination=IF_CONTAMINATION,
            random_state=42,
            n_estimators=100,
        )
        state.if_model.fit(X)
        state.if_reading_count = 0   # reset counter after retrain

        logger.info(
            "Isolation Forest trained for %s on %d readings (%d metrics)",
            state.device_id, min_len, len(metric_keys),
        )

    def _check_isolation_forest(
        self,
        state:     DeviceState,
        device_id: str,
        timestamp: datetime,
        metrics:   dict,
    ) -> AnomalyResult | None:

        if state.if_model is None:
            # model not trained yet — train now
            self._train_isolation_forest(state)
            if state.if_model is None:
                return None

        metric_keys = sorted(state.history.keys())

        # build feature vector for current reading
        try:
            x = np.array([[
                metrics.get(m, np.mean(state.history[m]))
                for m in metric_keys
            ]])
        except Exception as exc:
            logger.warning("Could not build IF feature vector: %s", exc)
            return None

        prediction = state.if_model.predict(x)    # 1 = normal, -1 = anomaly
        score      = state.if_model.score_samples(x)[0]  # lower = more anomalous

        if prediction[0] != -1:
            return None

        # anomaly detected — identify which metrics deviate most
        means       = {m: np.mean(state.history[m]) for m in metric_keys}
        deviations  = {
            m: abs(metrics.get(m, means[m]) - means[m])
            for m in metric_keys
        }
        top_metrics = sorted(
            deviations, key=deviations.get, reverse=True
        )[:2]

        return AnomalyResult(
            device_id=device_id,
            metric="isolation_forest",
            value=score,
            severity="WARNING",
            detection_layer="isolation_forest",
            message=(
                f"Multivariate anomaly detected on {device_id} "
                f"(anomaly score {score:.3f}). "
                f"Most deviating metrics: {', '.join(top_metrics)}. "
                f"No single metric crossed a threshold — combined pattern "
                f"is statistically unusual."
            ),
            timestamp=timestamp,
        )