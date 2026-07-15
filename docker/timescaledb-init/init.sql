-- ============================================================================
-- 🛠️ EXTENSIONS & SYSTEM INITIALISATION
-- ============================================================================
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================================
-- 📈 TIME-SERIES LEDGER: TELEMETRY
-- ============================================================================
CREATE TABLE IF NOT EXISTS telemetry (
    timestamp   TIMESTAMPTZ NOT NULL,
    device_id   VARCHAR(50) NOT NULL,
    country     VARCHAR(10) NOT NULL,
    city        VARCHAR(10) NOT NULL,
    device_type VARCHAR(20) NOT NULL,
    unit_id     VARCHAR(10) NOT NULL,
    metrics     JSONB       NOT NULL,
    scenario    VARCHAR(30) NULL        -- simulator only, null in production
);

-- Convert to TimescaleDB hypertable partitioned by time
SELECT create_hypertable('telemetry', 'timestamp', if_not_exists => TRUE);

-- Composite index for real-time asset tracking and dashboards
CREATE INDEX IF NOT EXISTS idx_telemetry_device_time
    ON telemetry (device_id, timestamp DESC);

-- Fleet-level filtering by device type
CREATE INDEX IF NOT EXISTS idx_telemetry_device_type
    ON telemetry (device_type, timestamp DESC);

-- JSONB index for metric-level queries
CREATE INDEX IF NOT EXISTS idx_telemetry_metrics
    ON telemetry USING GIN (metrics);

-- ============================================================================
-- 🚨 INCIDENT LOGS: ALERT HISTORY
-- ============================================================================
CREATE TABLE IF NOT EXISTS alert_history (
    alert_id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id       VARCHAR(50) NOT NULL,
    device_type     VARCHAR(20) NOT NULL,
    metric          VARCHAR(50) NOT NULL,
    value           FLOAT       NOT NULL,
    timestamp       TIMESTAMPTZ DEFAULT NOW(),
    severity        VARCHAR(20) NOT NULL
                    CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
    detection_layer VARCHAR(30) NOT NULL
                    CHECK (detection_layer IN (
                        'threshold', 'rolling_zscore', 'isolation_forest'
                    )),
    message         TEXT        NOT NULL,
    first_seen TIMESTAMPTZ DEFAULT NOW()
);

-- Cooldown enforcement: check if same alert fired recently
CREATE INDEX IF NOT EXISTS idx_alert_history_cooldown
    ON alert_history (device_id, metric, timestamp DESC);

-- Dashboard: filter by severity
CREATE INDEX IF NOT EXISTS idx_alert_history_severity
    ON alert_history (severity, timestamp DESC);

-- ============================================================================
-- ⚠️ QUARANTINE LEDGER: PIPELINE FAILURES (DLQ)
-- ============================================================================
CREATE TABLE IF NOT EXISTS failures (
    failure_id   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_payload  TEXT        NOT NULL,
    error_reason TEXT        NOT NULL,
    received_at  TIMESTAMPTZ DEFAULT NOW(),
    resolved     BOOLEAN     DEFAULT FALSE
);

-- Dashboard: unresolved failures
CREATE INDEX IF NOT EXISTS idx_failures_unresolved
    ON failures (resolved, received_at DESC);

-- ============================================================================
-- 🏥 DEVICE HEALTH: CURRENT STATUS
-- ============================================================================
CREATE TABLE IF NOT EXISTS device_status (
    device_id    VARCHAR(50)  PRIMARY KEY,
    device_type  VARCHAR(20)  NOT NULL,
    status       VARCHAR(20)  NOT NULL DEFAULT 'healthy'
                 CHECK (status IN ('healthy', 'unhealthy', 'disconnected')),
    since        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),  -- when status last changed
    last_seen    TIMESTAMPTZ  NOT NULL DEFAULT NOW()   -- last reading received
);

CREATE INDEX IF NOT EXISTS idx_device_status_status
    ON device_status (status);

-- ============================================================================
-- 🔴 CURRENT ALERTS: LATEST ALERT PER DEVICE+METRIC
-- ============================================================================
CREATE TABLE IF NOT EXISTS current_alerts (
    device_id       VARCHAR(50)  NOT NULL,
    metric          VARCHAR(50)  NOT NULL,
    value           FLOAT        NOT NULL,
    severity        VARCHAR(20)  NOT NULL
                    CHECK (severity IN ('INFO', 'WARNING', 'CRITICAL')),
    detection_layer VARCHAR(30)  NOT NULL
                    CHECK (detection_layer IN (
                        'threshold', 'rolling_zscore', 'isolation_forest'
                    )),
    message         TEXT         NOT NULL,
    first_seen      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),  -- when alert started
    last_updated    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),  -- last time it fired
    PRIMARY KEY (device_id, metric)
);
