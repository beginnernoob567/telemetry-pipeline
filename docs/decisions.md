# Architecture & Design Decisions

This document records the key decisions made in building the telemetry pipeline,
and the reasoning behind each one. Where the assignment left something open-ended,
this is where we explain what we chose and why.

---

## 1. Device fleet — what we simulate and why

**Decision:** 11 devices — 6 chillers and 4 pump-motors — only 10 emit real telemetry — "CHIL_07" is a dedicated malformed-payload generator - all located in a
simulated Bangalore data centre (`IN_BLR*\*` naming convention).

**Reasoning:**
The assignment says "keep the fleet small — a handful of devices with a few
metrics each." We interpreted this as enough devices to produce meaningful
fleet-level patterns while keeping the data volume manageable.

Chillers and pump-motors are the two most critical mechanical systems in a data
centre. Chillers manage coolant pressure and flow to prevent overheating; pump-
motors drive that coolant circulation and are the most common point of mechanical
failure.

**Device IDs:**

```
IN_BLR_CHIL_01 through IN_BLR_CHIL_06   (chillers)
IN_BLR_CHIL_07 → for unprocessable messages, goes to failures table.
IN_BLR_PUMO_01 through IN_BLR_PUMO_04   (pump-motors)
```

**Device ID naming convention:** `<COUNTRY>_<CITY>_<EQUIPMENT_TYPE>_<ID>`

For example, `IN_BLR_CHIL_01` breaks down as:

- `IN` — India
- `BLR` — Bangalore
- `CHIL` — Chiller
- `01` — unit number

This convention makes the ID self-describing and parseable. The consumer splits
on `_` to extract country, city, and equipment type without needing a separate
lookup table — useful for filtering and grouping at query time.

**Metrics per device type:**

| Device type | Metric               | Unit  | Normal range |
| ----------- | -------------------- | ----- | ------------ |
| Chiller     | coolant_pressure_psi | psi   | 100 – 140    |
| Chiller     | flow_rate_l_min      | L/min | 38 – 52      |
| Chiller     | ambient_temp_c       | °C    | 16 – 22      |
| Pump-motor  | vibration_rms_mm_s   | mm/s  | 0.5 – 3.0    |
| Pump-motor  | temperature_c        | °C    | 35 – 55      |
| Pump-motor  | current_draw_amps    | A     | 10 – 15      |

---

## 2. Message broker — Redpanda over Kafka

**Decision:** Redpanda as the streaming backbone.

**Reasoning:**
Redpanda is Kafka-compatible (same producer/consumer API) but runs as a single
binary with no ZooKeeper dependency. For a self-contained assignment that needs
to spin up cleanly with `docker compose up`, Redpanda is significantly simpler
to operate without sacrificing any of the architectural signal that using a
message broker provides.

The choice demonstrates familiarity with event-driven, streaming architectures
without the operational overhead of a full Kafka cluster.

**Two topics, partitioned by device_id:**

- `telemetry/cooling-units` — chiller readings
- `telemetry/pump-motors` — pump-motor readings
- `telemetry/failures` — unprocessable or corrupt messages (dead-letter)

Partitioning by `device_id` guarantees that all messages from a given device
arrive in order within a partition. This is important for trend detection —
out-of-order messages across devices are fine, but within a device we want
temporal ordering preserved.

---

## 3. Database — TimescaleDB over plain PostgreSQL or InfluxDB

**Decision:** TimescaleDB (PostgreSQL extension) for all storage.

**Reasoning:**
TimescaleDB extends PostgreSQL with hypertables and `time_bucket()` queries,
making time-series aggregations (rolling averages, windowed analysis) clean and
readable without leaving the SQL ecosystem. It also means we get full PostgreSQL
semantics (ACID, JOINs, indexes) for the alerts table, which is relational data,
not time-series.

Plain PostgreSQL would work but would require manual partitioning and less
expressive time-series queries. InfluxDB is purpose-built for time-series but
uses a different query language and would require a separate store for the alerts
table. TimescaleDB gives us both in one engine.

---

## 4. Anomaly detection — layered approach

**Decision:** Three layers of detection, applied in order.

**Reasoning:**
No single technique is sufficient across all failure modes. We use:

### Layer 1: Hardcoded thresholds

The first and fastest check. Each metric has a defined safe range (see §1).
A reading outside that range is immediately flagged regardless of history.

This catches sudden, catastrophic failures (a pressure drop to zero, a
temperature spike to 90°C) from the very first reading. It requires no history
and no model warm-up.

### Layer 2: Rolling z-score (scipy)

Applied once a device has at least 10 readings in the database. Computes the
z-score of the current reading against a rolling window of the last 30 readings
for that device and metric.

```
z = (current_value - rolling_mean) / rolling_std
```

A z-score above 2.5 flags a statistical anomaly — the reading is more than 2.5
standard deviations from recent behaviour. This catches gradual drift that never
crosses a hard threshold, and brief spikes that are large relative to the
device's own baseline.

**Spike suppression:** a single anomalous reading followed immediately by a
normal reading is classified as noise and does not generate an alert. We require
2 consecutive anomalous readings before firing. This directly addresses the
assignment's hint about "brief spikes that correct themselves."

### Layer 3: Isolation Forest (scikit-learn)

Applied once a device has at least 50 readings trained per individual device.

Isolation Forest detects anomalies that no single metric reveals alone — for
example, a pump-motor running at normal temperature but with elevated vibration
_and_ elevated current draw. Neither metric alone crosses a threshold, but
together they indicate bearing wear.

The model is retrained every 100 new readings per device to adapt to long-term
baseline shifts (e.g. seasonal ambient temperature change).

**Why not ML-only?** Isolation Forest needs data to be useful. On startup, with
a new device, or after a fleet expansion there may not be enough history. Layers
1 and 2 ensure detection works from reading zero.

---

## 5. Simulator — scenarios, not random noise

**Decision:** The simulator runs named scenarios rather than purely random data.

**Reasoning:**
The assignment asks us to decide what "realistic data looks like." Random noise
with occasional spikes produces uninteresting, hard-to-verify output. Named
scenarios produce observable, demonstrable failure patterns — much better for
the screen recording and for showing the detection logic actually works.

**Scenarios implemented:**

| Scenario              | Description                                                          |
| --------------------- | -------------------------------------------------------------------- |
| `healthy`             | Values within normal range, small Gaussian noise                     |
| `gradual_degradation` | One metric drifts slowly toward (and past) threshold over ~5 minutes |
| `sudden_spike`        | A metric jumps to a critical value for 75 readings, then recovers    |
| `sensor_dropout`      | Readings stop arriving for a device for 30–60 seconds                |
| `out_of_order`        | Messages arrive with timestamps slightly in the past                 |
| `duplicate`           | The same reading is published twice                                  |

Each device is assigned a scenario at startup. Some devices stay healthy
throughout to make the alert panel meaningful — not everything should be on fire.

**Sensor zero vs broken equipment:**
As the assignment hints, a reading of zero may mean a faulty sensor rather than
broken equipment. We handle this by flagging zero readings as `SENSOR_SUSPECT`
rather than `CRITICAL`, and routing them to the failures topic for investigation.

---

## 6. Consumer — sequential write-then-analyse, manual commit

**Decision:** Write to TimescaleDB first, analyse second, commit Redpanda offset
last. All in a single consumer process.

**Reasoning:**
This ordering is important for correctness:

1. **Write first** — analysis queries recent history from the DB. The current
   reading must be in the DB before we query, or the rolling window is one
   reading behind.
2. **Analyse second** — anomaly detection runs against the stored data.
3. **Commit last** — the Redpanda offset is committed only after both write and
   analysis succeed. If the process crashes mid-analysis, the message is
   replayed on restart. Nothing is silently lost.

Auto-commit is disabled (`enable.auto.commit=False`).

**Poison pills** (corrupt or unparseable messages) are committed immediately
after routing to `telemetry/failures`. We do not retry them — replaying a
corrupt message indefinitely would stall the consumer.

**Why a single process, not microservices?**
Splitting into separate writer and analyser services would require a second
message bus or shared state mechanism between them. For this scope, that
complexity adds no value. The boundary is clean in code even if it runs in one
process.

---

## 7. Operator interface — Streamlit over Grafana

**Decision:** Streamlit for the operator dashboard.

**Reasoning:**
Grafana is the industry-standard choice for time-series dashboards but has two
drawbacks for this submission: (1) dashboard configuration lives in JSON, not
Python, making the reviewer read two languages; (2) setup and provisioning takes
meaningful time away from the core service.

Streamlit is pure Python, co-located with the rest of the codebase, and makes
the dashboard logic readable in a code review. The reviewer can open
`dashboard.py` and immediately see _what_ we're showing and _why_ — which maps
directly to what the assignment asks for.

**Auto-refresh:** Streamlit's `st_autorefresh` component polls every 10 seconds,
giving the operator a near-live view without manual page reloads.

---

## 8. Alert design — persist, don't flood

**Decision:** Alerts are written to a dedicated `alerts` table in TimescaleDB
and displayed in the Streamlit panel. No external delivery (Slack, email,
PagerDuty) in this submission.

**Reasoning:**
The assignment explicitly warns against "a flood of alerts." Our alert logic
applies three suppression rules:

1. **Consecutive threshold** — at least 2 consecutive anomalous readings required
   before an alert fires (Layer 2 above).
2. **Cooldown** — once an alert fires for a device+metric pair, the same alert
   cannot fire again for 5 minutes. This prevents a degrading device from
   generating hundreds of identical alerts.
3. **Severity levels** — `INFO`, `WARNING`, `CRITICAL`. Only WARNING and above
   are shown prominently in the dashboard. INFO is logged for audit purposes.

External alert delivery (webhook, email) is a natural next step — the alert
record contains all necessary fields (`device_id`, `metric`, `severity`,
`message`, `timestamp`). Adding a notifier service that consumes the alerts
table would be straightforward. We note this as a known extension point, not an
omission.

---

## 9. Infrastructure — Docker Compose

**Decision:** The entire stack runs with `docker compose up`.

**Reasoning:**
A reviewer should be able to clone the repo, run one command, and see the
service working within 2 minutes. Docker Compose achieves this without requiring
any local installation of Redpanda, PostgreSQL, or TimescaleDB.

**Services in the compose file:**

- `redpanda` — message broker
- `timescaledb` — storage
- `simulator` — device data generator
- `consumer` — ingestion, analysis, alert writing
- `dashboard` — Streamlit operator interface

Startup order is managed via `depends_on` with health checks, so the simulator
and consumer do not start until Redpanda and TimescaleDB are ready.

---

## 10. What we deliberately did not build

- **Machine learning model persistence** — the Isolation Forest model is held
  in memory and rebuilt on restart. Persisting it to disk (via joblib or
  similar) is a straightforward addition.
- **External alert delivery** — see §8.
- **Historical replay** — the simulator generates live data only. Replaying a
  historical dataset is a known extension point.
- **Authentication on the dashboard** — the Streamlit interface is unauthenticated.
  In production this would sit behind an identity provider.
- **Horizontal scaling** — the consumer is a single process. Scaling to multiple
  consumers with Redpanda consumer groups is architecturally supported (partition
  by device_id already enables this) but not implemented here.
