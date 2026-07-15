# Telemetry Pipeline

A service that ingests real-time sensor data from a fleet of industrial devices, detects anomalies, and surfaces what needs an operator's attention.

## What it does

A simulated fleet of 10 devices — 6 chillers and 4 pump-motors in a Bangalore data centre — continuously emit sensor readings. The pipeline ingests those readings, stores them, runs anomaly detection, and presents the operator with a live dashboard showing fleet health and active alerts.

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
| Message broker | Redpanda                 | Two topics partitioned by device_id — `telemetry/cooling-units`, `telemetry/pump-motors`. Dead-letter topic `telemetry/failures` for unprocessable messages |
| Consumer       | Python                   | Validates, stores, and analyses every reading. Writes alerts when anomalies are detected                                                                    |
| Storage        | TimescaleDB (PostgreSQL) | Three tables — `telemetry`, `alerts`, `failures`                                                                                                            |
| Dashboard      | Streamlit                | Live operator view of fleet state, active alerts, and per-device metric charts. Auto-refreshes every 10 seconds                                             |

Full architecture and tool selection reasoning: [`docs/decisions.md`](docs/decisions.md)

---

## Device fleet

10 devices following the naming convention `<COUNTRY>_<CITY>_<EQUIPMENT_TYPE>_<ID>`:

```
IN_BLR_CHIL_01 → IN_BLR_CHIL_06    (chillers)
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

## How to run

**Prerequisites:** Docker and Docker Compose.

```bash
git clone <repo-url>
cd telemetry_pipeline
cp .env.example .env
docker compose up --build
```

That's it. All services start in the correct order via health checks.

| Service             | URL                   |
| ------------------- | --------------------- |
| Streamlit dashboard | http://localhost:8501 |
| Redpanda console    | http://localhost:8080 |
| TimescaleDB         | localhost:5432        |

To stop:

```bash
docker compose down
```

To stop and wipe all data:

```bash
docker compose down -v
```

---

## Environment variables

Copy `.env.example` to `.env` before running. All variables have sensible defaults for local development.

```
REDPANDA_BROKERS=redpanda:9092
POSTGRES_HOST=timescaledb
POSTGRES_PORT=5432
POSTGRES_DB=telemetry
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres
SIMULATOR_TICK_INTERVAL_MS=1000
DASHBOARD_REFRESH_SECONDS=10
```

---

## Database schema

Three tables in TimescaleDB:

- **`telemetry`** — every valid sensor reading. Hypertable partitioned by time. Metrics stored as JSONB, device ID components extracted as columns for fast fleet-level filtering.
- **`alert_history`** — anomaly notifications with severity, detection layer, and a human-readable message for the operator.
- **`failures`** — raw payloads of unprocessable messages with error reason and a `resolved` flag for operator triage.

Full schema with DDL and example rows: [`docs/schema.md`](docs/schema.md)

---

## Project structure

```
telemetry_pipeline/
├── python_services/
│   ├── common/                     # Shared models and device utilities
│   │   ├── models.py               # Pydantic models for telemetry reading
│   │   └── device_utils.py         # device_id parser, metric constants
│   ├── telemetry_device_sim/       # Simulator service
│   ├── telemetry_device_consumer/  # Consumer + anomaly detection
│   └── streamlit_dashboard/        # Operator dashboard
├── docker/
│   └── timescaledb-init/
│       └── init.sql                # Schema DDL — single source of truth
├── docs/
│   ├── decisions.md                # Why we built it this way
│   └── schema.md                   # Database schema reference
├── docker-compose.yml
├── .env
└── .env.example
```

---

## What's out of scope

Intentional descoping — see [`docs/decisions.md §10`](docs/decisions.md) for full reasoning:

- External alert delivery (Slack, email, PagerDuty) — the alert record contains everything needed; adding a notifier is a straightforward extension
- Isolation Forest model persistence — model is held in memory and rebuilds on restart
- Dashboard authentication
- Horizontal consumer scaling — architecturally supported via Redpanda consumer groups, not implemented

---

## Assumptions

- A reading of zero is treated as `SENSOR_SUSPECT` rather than `CRITICAL` — a zero may indicate a faulty sensor, not broken equipment
- A single anomalous reading followed by a normal reading is classified as noise and does not generate an alert
- Once an alert fires for a device+metric pair, the same alert is suppressed for 5 minutes to prevent alert floods
- The Isolation Forest model is considered reliable only after 50 readings per device; Layers 1 and 2 cover the warm-up period

---

_Full design reasoning in [`docs/decisions.md`](docs/decisions.md)_

---
