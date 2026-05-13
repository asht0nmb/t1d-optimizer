-- =============================================================================
-- t1d-engine bootstrap migration 0001 -- initial schema
-- =============================================================================
--
-- This is migration 0001, the initial Supabase Postgres schema for the
-- t1d-engine project. Future migrations live alongside this file and are
-- numbered sequentially: 0002_*.sql, 0003_*.sql, and so on.
--
-- The file is idempotent: re-running it against a database that already has
-- this schema is a no-op. Enums are wrapped in DO blocks that swallow
-- duplicate_object errors (Postgres has no CREATE TYPE IF NOT EXISTS); tables
-- and indexes use IF NOT EXISTS. COMMENT statements are unconditional because
-- they always overwrite.
--
-- Schema = public. RLS, roles, and policies are intentionally NOT created
-- here; they are deferred per the bootstrap spec.
--
-- Tables created (12 total):
--   1. cgm             -- 5-minute CGM readings, includes backfilled samples.
--   2. bolus           -- Delivered insulin boluses keyed by per-pump bolus_id.
--   3. requests        -- The user-or-pump request that produced each bolus.
--   4. basal           -- Per-minute commanded basal rate stream.
--   5. suspension      -- Pump suspend/resume intervals (user, alarm, PLGS).
--   6. events          -- Discrete pump events (site change, CGM session, ...).
--   7. alarms          -- Pump alarm/alert/cgm_alert lifecycle records.
--   8. site_issues     -- Derived occlusion clusters and resolution times.
--   9. cgm_gaps        -- Derived CGM data-loss intervals (closed or ongoing).
--  10. alerts_sent     -- Live-alert dedup state with partial-unique event_ref.
--  11. fetch_state     -- Per-source incremental sync bookmarks.
--  12. detection_config-- Runtime-tunable detection thresholds (intentionally empty).
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Enums
-- -----------------------------------------------------------------------------

DO $$ BEGIN
    CREATE TYPE bolus_source AS ENUM ('user', 'auto', 'override', 'unknown');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE bolus_category AS ENUM (
        'auto_correction',
        'user_meal',
        'user_meal_and_correction',
        'user_correction_only',
        'override_up',
        'override_down',
        'unknown'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE basal_rate_source AS ENUM (
        'algorithm',
        'profile',
        'suspended',
        'temp_rate',
        'temp_rate_and_algorithm',
        'unknown'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE suspend_reason AS ENUM (
        'user',
        'alarm',
        'malfunction',
        'plgs_auto',
        'unknown'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE pump_event_type AS ENUM (
        'site_change',
        'cgm_session',
        'mode_change',
        'pcm_change',
        'daily_marker'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE alarm_category AS ENUM ('alarm', 'alert', 'cgm_alert');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE alarm_action AS ENUM ('activated', 'cleared', 'ack');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- -----------------------------------------------------------------------------
-- cgm
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cgm (
    pump_serial       text        NOT NULL,
    seqnum            bigint      NOT NULL,
    timestamp         timestamptz NOT NULL,
    bg_mgdl           smallint    NOT NULL,
    backfilled        boolean     NOT NULL,
    sensor_timestamp  timestamptz NULL,
    PRIMARY KEY (pump_serial, seqnum)
);

CREATE INDEX IF NOT EXISTS cgm_pump_ts_idx
    ON cgm (pump_serial, timestamp DESC);

COMMENT ON TABLE cgm IS
    'CGM readings. PK is (pump_serial, seqnum) because timestamps are non-unique under backfill.';
COMMENT ON COLUMN cgm.bg_mgdl IS
    'Observed range 0..646; sentinel 0s preserved from parquet to retain raw data quality.';
COMMENT ON COLUMN cgm.sensor_timestamp IS
    'Non-null iff backfilled = true; original sensor time before pump backfill stamping.';


-- -----------------------------------------------------------------------------
-- bolus
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bolus (
    pump_serial    text          NOT NULL,
    bolus_id       integer       NOT NULL,
    timestamp      timestamptz   NOT NULL,
    insulin_units  numeric(6,3)  NOT NULL,
    PRIMARY KEY (pump_serial, bolus_id)
);

CREATE INDEX IF NOT EXISTS bolus_pump_ts_idx
    ON bolus (pump_serial, timestamp DESC);

COMMENT ON COLUMN bolus.bolus_id IS
    'Per-pump bolus id; max ~6111 observed historically.';
COMMENT ON COLUMN bolus.insulin_units IS
    'Delivered insulin in units, range 0..25.000; numeric(6,3) prevents float drift.';


-- -----------------------------------------------------------------------------
-- requests
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS requests (
    pump_serial         text          NOT NULL,
    bolus_id            integer       NOT NULL,
    timestamp           timestamptz   NOT NULL,
    carbs_g             smallint      NOT NULL,
    bg_mgdl             smallint      NOT NULL,
    iob                 numeric(6,3)  NOT NULL,
    bolus_source        bolus_source  NOT NULL,
    food_insulin        numeric(6,3)  NOT NULL,
    correction_insulin  numeric(6,3)  NOT NULL,
    total_requested     numeric(6,3)  NOT NULL,
    bolus_category      bolus_category NOT NULL,
    override_delta      numeric(6,3)  NULL,
    PRIMARY KEY (pump_serial, bolus_id)
);

CREATE INDEX IF NOT EXISTS requests_pump_ts_idx
    ON requests (pump_serial, timestamp DESC);

COMMENT ON COLUMN requests.bolus_id IS
    'Logical FK to bolus(pump_serial, bolus_id); not declared as a real FK because one historical orphan request exists.';
COMMENT ON COLUMN requests.carbs_g IS
    'Carbs grams entered with the request, 0..150 observed.';
COMMENT ON COLUMN requests.bg_mgdl IS
    'Fingerstick BG entered with the request; includes 0 for missing fingerstick (documented in DATA_NOTES).';
COMMENT ON COLUMN requests.override_delta IS
    'Non-null only when bolus_source = override; signed delta vs algorithm recommendation.';


-- -----------------------------------------------------------------------------
-- basal
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS basal (
    pump_serial      text               NOT NULL,
    timestamp        timestamptz        NOT NULL,
    commanded_rate   numeric(6,3)       NOT NULL,
    rate_source      basal_rate_source  NOT NULL,
    PRIMARY KEY (pump_serial, timestamp)
);

COMMENT ON TABLE basal IS
    'Commanded basal rate stream; PK (pump_serial, timestamp) covers the dominant query, no extra index needed.';
COMMENT ON COLUMN basal.commanded_rate IS
    'Units per hour; pre-divided by 1000 from raw milliunits during ingestion.';


-- -----------------------------------------------------------------------------
-- suspension
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS suspension (
    pump_serial          text              NOT NULL,
    suspend_timestamp    timestamptz       NOT NULL,
    resume_timestamp     timestamptz       NULL,
    duration_minutes     numeric(10,3)     NULL,
    suspend_reason       suspend_reason    NOT NULL,
    insulin_at_suspend   smallint          NOT NULL,
    pairing_suspect      boolean           NOT NULL,
    alarm_id             smallint          NULL,
    alarm_name           text              NULL,
    PRIMARY KEY (pump_serial, suspend_timestamp)
);

CREATE INDEX IF NOT EXISTS suspension_pump_resume_idx
    ON suspension (pump_serial, resume_timestamp);

COMMENT ON COLUMN suspension.insulin_at_suspend IS
    'IOB-equivalent reading at suspend, 0..275 observed.';
COMMENT ON COLUMN suspension.alarm_id IS
    'Pump alarm id that triggered the suspension; only ResumePumpAlarm2 (23) seen so far.';


-- -----------------------------------------------------------------------------
-- events
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS events (
    pump_serial       text             NOT NULL,
    seqnum            bigint           NOT NULL,
    timestamp         timestamptz      NOT NULL,
    event_type        pump_event_type  NOT NULL,
    event_subtype     text             NOT NULL,
    previous_mode     text             NULL,
    details           jsonb            NOT NULL,
    forced_by_alarm   boolean          NULL,
    PRIMARY KEY (pump_serial, seqnum)
);

CREATE INDEX IF NOT EXISTS events_pump_ts_idx
    ON events (pump_serial, timestamp DESC);

CREATE INDEX IF NOT EXISTS events_pump_type_ts_idx
    ON events (pump_serial, event_type, timestamp DESC);

COMMENT ON COLUMN events.event_subtype IS
    'Open vocabulary; includes dynamic unknown_<n> values for unmapped subtypes.';
COMMENT ON COLUMN events.previous_mode IS
    'Only populated for event_type = mode_change rows.';
COMMENT ON COLUMN events.details IS
    'Parsed JSON payload; parquet stores as JSON-encoded text and bootstrap parses on insert.';
COMMENT ON COLUMN events.forced_by_alarm IS
    'NULL for non-site_change rows; boolean only meaningful on site_change events.';


-- -----------------------------------------------------------------------------
-- alarms
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS alarms (
    pump_serial   text            NOT NULL,
    seqnum        bigint          NOT NULL,
    timestamp     timestamptz     NOT NULL,
    category      alarm_category  NOT NULL,
    action        alarm_action    NOT NULL,
    alarm_id      smallint        NOT NULL,
    alarm_name    text            NOT NULL,
    param1        bigint          NULL,
    param2        numeric(10,3)   NULL,
    PRIMARY KEY (pump_serial, seqnum)
);

CREATE INDEX IF NOT EXISTS alarms_pump_ts_idx
    ON alarms (pump_serial, timestamp DESC);

CREATE INDEX IF NOT EXISTS alarms_pump_name_action_ts_idx
    ON alarms (pump_serial, alarm_name, action, timestamp DESC);

COMMENT ON INDEX alarms_pump_name_action_ts_idx IS
    'Supports enrichment scans for OcclusionAlarm, cgm_out_of_range, BatteryShutdownAlarm.';
COMMENT ON COLUMN alarms.alarm_id IS
    'Pump alarm id, 0..54 observed.';
COMMENT ON COLUMN alarms.alarm_name IS
    '51 distinct values observed; includes dynamic unknown_<n> for unmapped ids.';
COMMENT ON COLUMN alarms.param1 IS
    'Raw uint32 parameter; bigint required because sentinel ~4.29e9 (UINT32_MAX) observed.';


-- -----------------------------------------------------------------------------
-- site_issues
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS site_issues (
    pump_serial                  text           NOT NULL,
    first_occlusion_ts           timestamptz    NOT NULL,
    last_occlusion_ts            timestamptz    NOT NULL,
    occlusion_count              smallint       NOT NULL,
    resolved_by_site_change_ts   timestamptz    NULL,
    resolution_delay_minutes     numeric(10,3)  NULL,
    PRIMARY KEY (pump_serial, first_occlusion_ts)
);

COMMENT ON TABLE site_issues IS
    'Derived occlusion clusters: one row per contiguous run of OcclusionAlarms ending in a site change.';


-- -----------------------------------------------------------------------------
-- cgm_gaps
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cgm_gaps (
    pump_serial       text           NOT NULL,
    start_ts          timestamptz    NOT NULL,
    end_ts            timestamptz    NULL,
    duration_minutes  numeric(10,3)  NULL,
    ongoing           boolean        NOT NULL,
    PRIMARY KEY (pump_serial, start_ts)
);

COMMENT ON TABLE cgm_gaps IS
    'Derived CGM data-loss intervals; closed gaps have end_ts/duration set, ongoing gaps do not.';
COMMENT ON COLUMN cgm_gaps.end_ts IS
    'NULL when ongoing = true; otherwise the resolved end of the gap.';


-- -----------------------------------------------------------------------------
-- alerts_sent
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS alerts_sent (
    id            bigserial    PRIMARY KEY,
    alert_kind    text         NOT NULL,
    fired_at      timestamptz  NOT NULL DEFAULT now(),
    pump_serial   text         NULL,
    event_ref     text         NULL,
    payload       jsonb        NOT NULL DEFAULT '{}'::jsonb,
    delivery      text         NOT NULL DEFAULT 'pending'
);

CREATE INDEX IF NOT EXISTS alerts_sent_kind_fired_idx
    ON alerts_sent (alert_kind, fired_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS alerts_sent_event_ref_uq
    ON alerts_sent (alert_kind, event_ref)
    WHERE event_ref IS NOT NULL;

COMMENT ON TABLE alerts_sent IS
    'Live-alert dedup state; one row per alert delivery attempt.';
COMMENT ON INDEX alerts_sent_event_ref_uq IS
    'Partial unique index on (alert_kind, event_ref) where event_ref IS NOT NULL. Supports per-event idempotent inserts while still allowing rows without an event_ref. Postgres requires the predicate to be respecified in ON CONFLICT to match a partial index, so callers must write: INSERT INTO alerts_sent (...) VALUES (...) ON CONFLICT (alert_kind, event_ref) WHERE event_ref IS NOT NULL DO NOTHING. Rows with NULL event_ref are not deduped (each insert produces a new row), which is intentional.';
COMMENT ON COLUMN alerts_sent.alert_kind IS
    'Alert kind identifier, e.g. anomaly_spike, missed_meal, site_failure.';
COMMENT ON COLUMN alerts_sent.pump_serial IS
    'Nullable: not all alert kinds are pump-scoped.';
COMMENT ON COLUMN alerts_sent.event_ref IS
    'Opaque dedup key; format owned by the detector that produced the alert.';
COMMENT ON COLUMN alerts_sent.delivery IS
    'Delivery status: pending | sent | failed.';


-- -----------------------------------------------------------------------------
-- fetch_state
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS fetch_state (
    source_id        text         PRIMARY KEY,
    source_kind      text         NOT NULL,
    last_synced_at   timestamptz  NULL,
    actual_min_date  date         NULL,
    actual_max_date  date         NULL,
    meta             jsonb        NOT NULL DEFAULT '{}'::jsonb,
    updated_at       timestamptz  NOT NULL DEFAULT now()
);

COMMENT ON TABLE fetch_state IS
    'Per-source incremental sync bookmarks for ingestion connectors.';
COMMENT ON COLUMN fetch_state.source_id IS
    'Source identifier; pump_serial for tconnectsync, the literal string pydexcom for the live CGM connector.';
COMMENT ON COLUMN fetch_state.source_kind IS
    'Connector kind: tconnectsync | pydexcom.';


-- -----------------------------------------------------------------------------
-- detection_config
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS detection_config (
    key          text         PRIMARY KEY,
    value        jsonb        NOT NULL,
    description  text         NULL,
    updated_at   timestamptz  NOT NULL DEFAULT now()
);

COMMENT ON TABLE detection_config IS
    'Runtime-tunable detection thresholds. Left empty by the bootstrap on purpose: the detection engine is being rewritten and seeding now would create stale data that the new code would have to migrate.';
COMMENT ON COLUMN detection_config.key IS
    'Dotted threshold key, e.g. anomaly_detection.spike_threshold.';
