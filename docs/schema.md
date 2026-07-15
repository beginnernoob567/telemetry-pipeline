# Database Schema Documentation: Telemetry Pipeline

This document defines the data structures used inside the TimescaleDB instance. It includes relational event structures and time-series hypertables optimized for high-velocity industrial IoT ingestion.

---

## Tables

### 1. `telemetry`

- **Type:** TimescaleDB Hypertable
- **Description:** Continuous ledger storing high-frequency metrics arriving from active edge devices. Metric structures vary across equipment profiles and are stored inside a flexible JSON format.

| Column Name   | Data Type     | Constraints / Defaults | Description                                                                                        |
| :------------ | :------------ | :--------------------- | :------------------------------------------------------------------------------------------------- |
| `timestamp`   | `TIMESTAMPTZ` | `NOT NULL`             | Exact time the edge device recorded the metrics. **(Hypertable Time Partition Key)**               |
| `device_id`   | `VARCHAR(50)` | `NOT NULL`             | Full Semantic Identifier string (e.g., `IN_BLR_CHILLER_01`).                                       |
| `country`     | `VARCHAR(10)` | `NOT NULL`             | Extracted country short code parsed from the Device ID (e.g., `IN`).                               |
| `city`        | `VARCHAR(10)` | `NOT NULL`             | Extracted city/plant identifier parsed from the Device ID (e.g., `BLR`).                           |
| `device_type` | `VARCHAR(30)` | `NOT NULL`             | Asset classification category (e.g., `CHILLER`, `MOTOR`, `PUMP`).                                  |
| `unit_id`     | `VARCHAR(10)` | `NOT NULL`             | End tracking designation index number (e.g., `01`, `02`).                                          |
| `metrics`     | `JSONB`       | `NOT NULL`             | Flexible key-value binary store capturing physical sensor readings specific to that asset profile. |

- **Composite Primary Key Index:** `PRIMARY KEY (timestamp, device_id)`

---

### 2. `alert_history`

- **Type:** Standard Relational Table
- **Description:** Log of parameter violations, operational anomalies, or safety threshold breaches captured by the pipeline ingestion worker.

| Column Name       | Data Type     | Constraints / Defaults                     | Description                                                                            |
| :---------------- | :------------ | :----------------------------------------- | :------------------------------------------------------------------------------------- |
| `alert_id`        | `UUID`        | `PRIMARY KEY`, `DEFAULT gen_random_uuid()` | Universally unique tracker string for the incident log.                                |
| `device_id`       | `VARCHAR(50)` | `NOT NULL`                                 | Target asset identifier experiencing operational issues.                               |
| `metric`          | `VARCHAR(50)` | `NOT NULL`                                 | Specific metric parameter that triggered the event (e.g., `vibration_rms_mm_s`).       |
| `value`           | `FLOAT`       | `NOT NULL`                                 | Raw numeric reading recorded at the time of the validation breach.                     |
| `timestamp`       | `TIMESTAMPTZ` | `DEFAULT NOW()`                            | The timestamp indicating when the ingestion framework caught the anomaly.              |
| `severity`        | `VARCHAR(20)` | `NOT NULL`                                 | Risk impact categorization level (e.g., `WARNING`, `CRITICAL`).                        |
| `detection_layer` | `VARCHAR(30)` | `NOT NULL`                                 | Analytics pipeline layer identifying the anomaly (e.g., `THRESHOLD_CHECK`, `Z_SCORE`). |
| `message`         | `TEXT`        | `NOT NULL`                                 | Human-readable system incident summary string displayed on operator dashboards.        |

---

### 3. `failures`

- **Type:** Standard Relational Table
- **Description:** Dead Letter Queue (DLQ) persistent storage layout. Traps malformed string payloads, corruption anomalies, or broken schemas safely outside the data flow.

| Column Name    | Data Type     | Constraints / Defaults                     | Description                                                                    |
| :------------- | :------------ | :----------------------------------------- | :----------------------------------------------------------------------------- |
| `failure_id`   | `UUID`        | `PRIMARY KEY`, `DEFAULT gen_random_uuid()` | Reference tracking index assigned to the diagnostic ticket.                    |
| `raw_payload`  | `TEXT`        | `NOT NULL`                                 | Raw plain text string caught falling through standard serialization processes. |
| `error_reason` | `TEXT`        | `NOT NULL`                                 | Accompanying Python language error execution detail stack string trace.        |
| `received_at`  | `TIMESTAMPTZ` | `DEFAULT NOW()`                            | Instant the payload was diverted into the database quarantine zone.            |
| `resolved`     | `BOOLEAN`     | `DEFAULT FALSE`                            | Toggled bit tracking flag indicating administrative review states.             |

---

## Sample Ingestion Payloads (`JSONB` Examples)

### Cooling Asset Profile Configuration Space (`CHILLER`)

```json
{
  "flow_rate_l_min": 45.2,
  "temperature_c": 19.8,
  "coolant_pressure_psi": 118.4
}
```

### Kinetic Power Asset Profile Configuration Space (`MOTOR` / `PUMP`)

```json
{
  "temperature_c": 42.1,
  "current_draw_amps": 11.8,
  "vibration_rms_mm_s": 1.45
}
```
