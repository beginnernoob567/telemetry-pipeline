# Telemetry Pipeline

A service that ingests real-time sensor data from a fleet of industrial devices, detects anomalies, and surfaces what needs an operator's attention.

## What it does

A simulated fleet of 11 devices — 7 chillers and 4 pump-motors in a data centre — continuously emit sensor readings. The pipeline ingests those readings, stores them, runs anomaly detection, and presents the operator with a live dashboard showing fleet health and active alerts.

The core question the service answers: **which devices need attention right now, and why?**

---

## Architecture

```
[Simulator] ──► [Redpanda] ──► [Consumer] ──► [TimescaleDB]
                                                     │
                                             [Streamlit Dashboard]
                                                     │
                                              [Operator]
```

| Component      | Technology               | Role                                                                                                                                                        |
| -------------- | ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Simulator      | Python                   | Generates realistic sensor readings across named failure scenarios                                                                                          |
| Message broker | Redpanda                 | Two topics partitioned by device_id — `telemetry.cooling-units`, `telemetry.pump-motors`. Dead-letter topic `telemetry.failures` for unprocessable messages |
| Consumer       | Python                   | Validates, deduplicates, stores, and analyses every reading. Writes alerts when anomalies are detected                                                      |
| Storage        | TimescaleDB (PostgreSQL) | Five tables — `telemetry`, `device_status`, `current_alerts`, `alert_history`, `failures`                                                                   |
| Dashboard      | Streamlit                | Live operator view of fleet state, active alerts, per-device metric charts, and paginated DLQ. Auto-refreshes every 10 seconds                              |

Full architecture and tool selection reasoning: [`docs/decisions.md`](docs/decisions.md)

---

## Device fleet

11 devices following the naming convention `<COUNTRY>_<CITY>_<EQUIPMENT_TYPE>_<ID>`:

```
IN_BLR_CHIL_01 → IN_BLR_CHIL_06    (chillers)
IN_BLR_CHIL_07 → for unprocessable messages, goes to failures table.
IN_BLR_PUMO_01 → IN_BLR_PUMO_04    (pump-motors)
```

**Chiller metrics:** `coolant_pressure_psi`, `flow_rate_l_min`, `ambient_temp_c`

**Pump-motor metrics:** `vibration_rms_mm_s`, `temperature_c`, `current_draw_amps`

---

## Anomaly detection

Three layers, applied in order on every reading:

**1. Hardcoded thresholds** — each metric has a defined safe range. A reading outside that range fires immediately, no history needed. Catches sudden catastrophic failures from reading zero.

**2. Rolling z-score** — once a device has 10+ readings, computes how far the current value is from its own recent baseline (30-reading window). Catches gradual drift that never crosses a hard threshold. Requires 2 consecutive anomalous readings to fire — single spikes are treated as noise.

**3. Isolation Forest** — once a device has 50+ readings, runs multivariate anomaly detection across all metrics together. Catches subtle correlated failures that no single metric reveals alone (e.g. slightly elevated vibration + slightly elevated current draw = likely bearing wear).

Each alert records which detection layer caught it, so the dashboard shows not just _what_ is wrong but _how_ it was detected.

Full detection logic and suppression rules: [`docs/decisions.md §4`](docs/decisions.md)

---

## Handling messy data

- **Duplicates** — the `telemetry` table has a `UNIQUE (device_id, timestamp, metrics)` constraint. A repeated reading is a no-op insert (`ON CONFLICT DO NOTHING`), and the consumer skips analysis entirely for a discarded duplicate so it can't double-count in the rolling z-score window.
- **Suspect zeros** — a reading of exactly `0.0` is flagged `INFO` rather than `CRITICAL`, since a zero is as likely to mean a dead sensor as a real equipment failure.
- **Brief spikes** — the rolling z-score layer requires 2 consecutive anomalous readings before it fires, so a single reading that corrects itself on the next tick is treated as noise, not an alert.
- **Out-of-order arrival** — a late-arriving reading is stored with its true (past) timestamp; TimescaleDB doesn't care about insert order. **Known limitation:** the in-memory rolling window used for z-score/Isolation Forest is ordered by arrival, not by timestamp, so a reading that arrives a few seconds late is analysed as if it were the newest — see [Known limitations](#known-limitations).
- **Sensor dropout** — the background watchdog marks a device `disconnected` if no reading has arrived within `DISCONNECTION_THRESHOLD_S` (default 30s), independent of whether any alert has fired.

---

## Simulator scenarios

Each device is assigned a scenario at startup. Not everything is on fire — some devices stay healthy throughout to make the alert panel meaningful.

| Scenario              | Description                                                         |
| --------------------- | ------------------------------------------------------------------- |
| `healthy`             | Normal readings with small Gaussian noise                           |
| `gradual_degradation` | One metric drifts slowly toward and past threshold over ~20 minutes |
| `sudden_spike`        | A metric jumps to a critical value for 1–2 readings then recovers   |
| `sensor_dropout`      | Readings stop arriving for 60–120 seconds                           |
| `out_of_order`        | Messages arrive with timestamps slightly in the past                |
| `duplicate`           | The same reading is published twice                                 |

---

## What's out of scope

Intentional descoping — see [`docs/decisions.md §10`](docs/decisions.md) for full reasoning:

- External alert delivery (Slack, email, PagerDuty) — the alert record contains everything needed; adding a notifier is a straightforward extension
- Isolation Forest model persistence — model is held in memory and rebuilds on restart
- Dashboard authentication
- Horizontal consumer scaling — architecturally supported via Redpanda consumer groups, not implemented

---

## Known limitations

Found during review, not yet fixed — noted here rather than silently left out:

- **Out-of-order readings aren't re-sorted before analysis.** They're stored with the correct timestamp, but the analyser's rolling window is ordered by arrival, so a reading that arrives a few seconds late is treated as the most recent one for z-score/Isolation Forest purposes. For the scale of lateness this pipeline simulates (a few seconds), the effect on the rolling baseline is negligible — but it's a real simplification, not a solved problem.
- **Isolation Forest alerts store a comma-joined metric list** (e.g. `"vibration_rms_mm_s, temperature_c"`) rather than a single metric name, since a multivariate anomaly doesn't map cleanly to one metric. This means Isolation Forest alerts don't currently render as markers on the per-metric charts in the device detail view — they do still appear in the alert history list and the fleet-level active alerts panel.

---

## Assumptions

- A reading of zero is treated as `SENSOR_SUSPECT` (`INFO` severity) rather than `CRITICAL` — a zero may indicate a faulty sensor, not broken equipment
- A single anomalous reading followed by a normal reading is classified as noise and does not generate an alert
- Once an alert fires for a device+metric pair, the same alert is suppressed for 5 minutes to prevent alert floods
- A duplicate reading (same device_id, timestamp, and metrics) is discarded at the storage layer and does not get re-analysed
- The Isolation Forest model is considered reliable only after 50 readings per device; Layers 1 and 2 cover the warm-up period

---

_Full design reasoning in [`docs/decisions.md`](docs/decisions.md)_
