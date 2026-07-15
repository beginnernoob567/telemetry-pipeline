## How to run

**Prerequisites:** Docker and Docker Compose.

```bash
git clone <repo-url>
cd telemetry-pipeline
cp .env.example .env
docker compose build --no-cache
docker compose up
```

To run docker in detached mode

```bash
docker compose up -d
```

> **Note:** `init.sql` only runs on first initialization of an empty database volume. If you've run this stack before and have an existing `timescaledb` volume, run `docker compose down -v` first so the current schema (including the dedup constraint) applies cleanly.

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

Five tables in TimescaleDB:

- **`telemetry`** — every valid, deduplicated sensor reading. Hypertable partitioned by time, with a `UNIQUE (device_id, timestamp, metrics)` constraint to guard against duplicate ingestion. Metrics stored as JSONB, device ID components extracted as columns for fast fleet-level filtering.
- **`device_status`** — current health (`healthy` / `unhealthy` / `disconnected`) per device — the materialized "latest state" the Fleet Overview screen reads directly.
- **`current_alerts`** — the live set of active alerts, one row per `(device_id, metric)` pair, upserted in place and deleted on resolution. This is what keeps the Active Alerts panel from flooding.
- **`alert_history`** — permanent, append-only audit log of every alert ever fired, including resolved ones. Backs the per-device timeline and the 5-minute alert cooldown check.
- **`failures`** — raw payloads of unprocessable messages with error reason and a `resolved` flag for operator triage.

Full schema with DDL, constraints, and example rows: [`docs/schema.md`](docs/schema.md)

---

## Project structure

```
telemetry-pipeline/
├── python_services/
│   ├── telemetry_device_sim/       # Simulator service
│   ├── telemetry_device_consumer/  # Consumer, validation, dedup, anomaly detection
│   │   ├── models.py               # Pydantic models for telemetry reading
│   │   ├── device_utils.py         # device_id parser, metric constants
│   │   ├── validator.py            # Message validation pipeline
│   │   ├── analyser.py             # Three-layer anomaly detection
│   │   ├── alerting.py             # Alert cooldown, upsert, resolution
│   │   ├── storage.py              # All DB reads/writes
│   │   └── consumer.py             # Main consume loop + watchdog
│   └── streamlit_dashboard/        # Operator dashboard
├── docker/
│   └── timescaledb-init/
│       └── init.sql                # Schema DDL — single source of truth
├── docs/
│   ├── decisions.md                # Why we built it this way
│   └── schema.md                   # Database schema reference
│   └── project.md                  # Project reference
├── docker-compose.yml
├── .env
└── .env.example
```

---
