# Database Schema Documentation: Telemetry Pipeline

This document defines the data structures used inside the TimescaleDB instance.
It covers the time-series hypertable and the four supporting relational tables,
and mirrors `docker/timescaledb-init/init.sql` exactly — if the two ever
disagree, `init.sql` is the source of truth.

---

## Tables

### 1. `telemetry`

- **Type:** TimescaleDB Hypertable (partitioned on `timestamp`)
- **Description:** Append-only ledger of every valid sensor reading ingested
  by the consumer. Metrics are stored as JSONB since the metric set differs
  by device type (chiller vs. pump-motor).

| Column        | Type          | Constraints / Defaults | Description                                                                                                                   |
| :------------ | :------------ | :--------------------- | :---------------------------------------------------------------------------------------------------------------------------- |
| `timestamp`   | `TIMESTAMPTZ` | `NOT NULL`             | Time the reading was recorded. Hypertable partition key.                                                                      |
| `device_id`   | `VARCHAR(50)` | `NOT NULL`             | Full device identifier, e.g. `IN_BLR_CHIL_01`.                                                                                |
| `country`     | `VARCHAR(10)` | `NOT NULL`             | Parsed from `device_id`, e.g. `IN`.                                                                                           |
| `city`        | `VARCHAR(10)` | `NOT NULL`             | Parsed from `device_id`, e.g. `BLR`.                                                                                          |
| `device_type` | `VARCHAR(20)` | `NOT NULL`             | Parsed from `device_id` — `CHIL` (chiller) or `PUMO` (pump-motor).                                                            |
| `unit_id`     | `VARCHAR(10)` | `NOT NULL`             | Parsed from `device_id`, e.g. `01`.                                                                                           |
| `metrics`     | `JSONB`       | `NOT NULL`             | Metric values for this reading, e.g. `{"vibration_rms_mm_s": 1.4, ...}`.                                                      |
| `scenario`    | `VARCHAR(30)` | `NULL`                 | Simulator-only field identifying the injected failure scenario. Always `NULL` in production (real sensors don't report this). |

**Constraints:**

- No primary key — a hypertable partitioned by time doesn't require one, and
  every reading is a distinct fact rather than a row to be updated in place.
- `UNIQUE (device_id, timestamp, metrics)` — **duplicate-ingestion guard.**
  If the same `(device_id, timestamp, metrics)` combination is published more
  than once — the simulator has a `duplicate` scenario for exactly this — the
  second insert is a no-op (`ON CONFLICT DO NOTHING` in `insert_telemetry`)
  rather than a second row. This also prevents a duplicate from double-feeding
  the in-memory rolling z-score window in the analyser.

**Indexes:**

- `idx_telemetry_device_time (device_id, timestamp DESC)` — per-device history
  queries (dashboard charts, rolling window lookups).
- `idx_telemetry_device_type (device_type, timestamp DESC)` — fleet-level
  filtering by equipment type.
- `idx_telemetry_metrics` — GIN index on `metrics` for ad-hoc JSONB queries.

---

### 2. `device_status`

- **Type:** Standard relational table, one row per device
- **Description:** Current health state of every device the pipeline has ever
  seen a reading from. This is what the Fleet Overview screen reads directly —
  it's a materialized "latest state," not a log.

| Column        | Type          | Constraints / Defaults                                                          | Description                                                                                 |
| :------------ | :------------ | :------------------------------------------------------------------------------ | :------------------------------------------------------------------------------------------ |
| `device_id`   | `VARCHAR(50)` | `PRIMARY KEY`                                                                   | Device identifier.                                                                          |
| `device_type` | `VARCHAR(20)` | `NOT NULL`                                                                      | `CHIL` or `PUMO`.                                                                           |
| `status`      | `VARCHAR(20)` | `NOT NULL DEFAULT 'healthy'`, `CHECK IN ('healthy','unhealthy','disconnected')` | Current device state.                                                                       |
| `since`       | `TIMESTAMPTZ` | `NOT NULL DEFAULT NOW()`                                                        | When `status` last changed — **not** updated on every reading, only on a status transition. |
| `last_seen`   | `TIMESTAMPTZ` | `NOT NULL DEFAULT NOW()`                                                        | Timestamp of the most recent reading received from this device, valid or duplicate.         |

**Status transitions:**

- `healthy → unhealthy`: an anomaly is detected and an alert fires (`alerting.py`).
- `unhealthy → healthy`: all active alerts for the device resolve (3 consecutive
  clean readings on every previously-alerting metric).
- `* → disconnected`: the background watchdog (`run_watchdog` in `consumer.py`)
  marks a device disconnected if `last_seen` is older than
  `DISCONNECTION_THRESHOLD_S` (default 30s).

**Index:** `idx_device_status_status (status)` — fleet summary counts.

---

### 3. `current_alerts`

- **Type:** Standard relational table, one row per active `(device_id, metric)` pair
- **Description:** The _live_ alert state — what's wrong right now. Rows are
  deleted once the underlying issue resolves, so this table only ever holds
  what an operator actually needs to act on. Read by the Fleet Overview's
  Active Alerts panel.

| Column            | Type          | Constraints / Defaults                                                   | Description                                                                                                                                               |
| :---------------- | :------------ | :----------------------------------------------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `device_id`       | `VARCHAR(50)` | part of `PRIMARY KEY`                                                    | Device the alert belongs to.                                                                                                                              |
| `metric`          | `VARCHAR(50)` | part of `PRIMARY KEY`                                                    | Metric name for threshold/z-score alerts. For Isolation Forest alerts this is a comma-joined list of the top deviating metrics, not a single metric name. |
| `value`           | `FLOAT`       | `NOT NULL`                                                               | Reading value (or anomaly score, for Isolation Forest) that triggered the alert.                                                                          |
| `severity`        | `VARCHAR(20)` | `NOT NULL`, `CHECK IN ('INFO','WARNING','CRITICAL')`                     | Alert severity.                                                                                                                                           |
| `detection_layer` | `VARCHAR(30)` | `NOT NULL`, `CHECK IN ('threshold','rolling_zscore','isolation_forest')` | Which layer caught it.                                                                                                                                    |
| `message`         | `TEXT`        | `NOT NULL`                                                               | Human-readable explanation shown on the dashboard.                                                                                                        |
| `first_seen`      | `TIMESTAMPTZ` | `NOT NULL DEFAULT NOW()`                                                 | When this alert first fired (unchanged across upserts).                                                                                                   |
| `last_updated`    | `TIMESTAMPTZ` | `NOT NULL DEFAULT NOW()`                                                 | Last time this device+metric re-fired (updated on every upsert).                                                                                          |

**Primary key:** `(device_id, metric)` — upserted, not appended. A repeated
alert for the same device+metric updates this row in place rather than
creating a new one; that's what keeps the Active Alerts panel from flooding.

---

### 4. `alert_history`

- **Type:** Standard relational table, append-only
- **Description:** Full audit log of every alert ever fired, including ones
  that have since resolved and been deleted from `current_alerts`. This is
  what the per-device detail page reads for its alert timeline and chart
  overlays, and what the cooldown check (`get_recent_alert`) queries against.

| Column            | Type          | Constraints / Defaults                                                   | Description                                                                                                  |
| :---------------- | :------------ | :----------------------------------------------------------------------- | :----------------------------------------------------------------------------------------------------------- |
| `alert_id`        | `UUID`        | `PRIMARY KEY DEFAULT gen_random_uuid()`                                  | Unique row identifier.                                                                                       |
| `device_id`       | `VARCHAR(50)` | `NOT NULL`                                                               | Device the alert belongs to.                                                                                 |
| `device_type`     | `VARCHAR(20)` | `NOT NULL`                                                               | `CHIL` or `PUMO`.                                                                                            |
| `metric`          | `VARCHAR(50)` | `NOT NULL`                                                               | Same caveat as `current_alerts.metric` — comma-joined for Isolation Forest.                                  |
| `value`           | `FLOAT`       | `NOT NULL`                                                               | Reading value or anomaly score at the time of the alert.                                                     |
| `timestamp`       | `TIMESTAMPTZ` | `DEFAULT NOW()`                                                          | When the alert fired.                                                                                        |
| `severity`        | `VARCHAR(20)` | `NOT NULL`, `CHECK IN ('INFO','WARNING','CRITICAL')`                     | Alert severity.                                                                                              |
| `detection_layer` | `VARCHAR(30)` | `NOT NULL`, `CHECK IN ('threshold','rolling_zscore','isolation_forest')` | Which layer caught it.                                                                                       |
| `message`         | `TEXT`        | `NOT NULL`                                                               | Human-readable explanation.                                                                                  |
| `first_seen`      | `TIMESTAMPTZ` | `DEFAULT NOW()`                                                          | Present for schema parity with `current_alerts`; not used for cooldown logic — the row's own `timestamp` is. |

**Indexes:**

- `idx_alert_history_cooldown (device_id, metric, timestamp DESC)` — backs the
  5-minute cooldown check that suppresses repeat alerts.
- `idx_alert_history_severity (severity, timestamp DESC)` — dashboard filtering.

**Note:** rows here are never updated or deleted — this is the one table in
the schema that's a true log, not a current-state snapshot.

---

### 5. `failures`

- **Type:** Standard relational table (Dead Letter Queue)
- **Description:** Unprocessable messages — corrupt JSON, schema validation
  failures, unknown device types. Routed here instead of crashing the
  consumer or silently dropping data.

| Column         | Type          | Constraints / Defaults                  | Description                                                                                             |
| :------------- | :------------ | :-------------------------------------- | :------------------------------------------------------------------------------------------------------ |
| `failure_id`   | `UUID`        | `PRIMARY KEY DEFAULT gen_random_uuid()` | Unique row identifier.                                                                                  |
| `raw_payload`  | `TEXT`        | `NOT NULL`                              | The raw message content that failed to process, truncated to 2000 chars at write time.                  |
| `error_reason` | `TEXT`        | `NOT NULL`                              | Why it failed — e.g. `"JSON decode error"`, `"Unknown device_type"`.                                    |
| `received_at`  | `TIMESTAMPTZ` | `DEFAULT NOW()`                         | When the failure was recorded.                                                                          |
| `resolved`     | `BOOLEAN`     | `DEFAULT FALSE`                         | Manual triage flag — not currently set anywhere in the pipeline; reserved for future operator workflow. |

**Index:** `idx_failures_unresolved (resolved, received_at DESC)` — DLQ panel query.

---

## Sample `metrics` JSONB payloads

### Chiller (`device_type = 'CHIL'`)

```json
{
  "coolant_pressure_psi": 118.4,
  "flow_rate_l_min": 45.2,
  "ambient_temp_c": 19.8
}
```

### Pump-motor (`device_type = 'PUMO'`)

```json
{
  "vibration_rms_mm_s": 1.45,
  "temperature_c": 42.1,
  "current_draw_amps": 11.8
}
```

---

## Table relationships at a glance

```
telemetry ──────────────► analyser (in-memory, not a table)
                                │
                                ▼
                         current_alerts  (live state — upserted, deleted on resolve)
                                │
                                ▼
                         alert_history   (permanent log — insert-only)

failures ◄── validator (parallel path, never touches telemetry or alerts)
```

`device_status` is updated from two independent places: `update_last_seen`
(every valid reading, Step 3 of the consumer) and `upsert_device_status`
(status transitions triggered by `alerting.py` and the watchdog) — that's why
`last_seen` and `status`/`since` are allowed to change independently of each
other in the DDL above.
