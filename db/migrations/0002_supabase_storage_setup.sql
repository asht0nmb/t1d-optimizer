-- =============================================================================
-- t1d-engine migration 0002 -- SupabaseStorage setup
-- =============================================================================
--
-- Lands two changes alongside the first SupabaseStorage release:
--
--   1. An idle-in-transaction timeout backstop. Belt-and-suspenders behind the
--      SupabaseStorage lifecycle discipline (open-do-close for short-lived
--      Vercel/Telegram/dashboard callers; caller-managed conn for long-lived
--      GitHub Action / bootstrap). Any session that holds a transaction open
--      for more than 5 minutes without activity is terminated automatically.
--
--   2. A `detection_results` table used by SupabaseStorage.record_detection_result.
--      Mirrors the minimal `DetectionResult` record shape in
--      core/storage/records.py: (kind, anchor_timestamp, payload, created_at).
--
-- Idempotent: re-running against a database that already has these changes is
-- a no-op. CREATE TABLE / CREATE INDEX use IF NOT EXISTS, and the ALTER DATABASE
-- statement is naturally idempotent.
--
-- Apply procedure (run by hand against the Supabase project's direct
-- connection, not from the agent):
--
--   psql "$SUPABASE_DB_URL" -f db/migrations/0002_supabase_storage_setup.sql
--
-- where SUPABASE_DB_URL is the Direct connection string
-- (port 5432, host db.<project>.supabase.co).
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Idle-in-transaction backstop
-- -----------------------------------------------------------------------------

-- Connection-leak backstop for serverless functions (Vercel cron, Telegram
-- webhooks, dashboard API routes). The SupabaseStorage lifecycle pattern
-- already keeps connections short-lived; this is the Postgres-side guarantee
-- behind it. The session-level setting only takes effect on NEW sessions, so
-- existing pooler-held sessions inherit the new default the next time they
-- are re-issued.
--
-- The literal database name "postgres" is hardcoded because that is the default
-- Supabase project database name and we don't run any other databases on the
-- cluster. If a future Supabase project renames the default database, update
-- this statement (or scope this setting at the role level instead).
ALTER DATABASE postgres SET idle_in_transaction_session_timeout = '5min';


-- -----------------------------------------------------------------------------
-- detection_results
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS detection_results (
    id                bigserial    PRIMARY KEY,
    kind              text         NOT NULL,
    anchor_timestamp  timestamptz  NOT NULL,
    payload           jsonb        NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS detection_results_kind_created_idx
    ON detection_results (kind, created_at DESC);

COMMENT ON TABLE detection_results IS
    'Triggered-detection log; one row per detection result emitted by the detection layer. Generic shape (kind / anchor_timestamp / payload / created_at) so detection families do not need their own tables — payload carries family-specific details.';
COMMENT ON COLUMN detection_results.kind IS
    'Detection family identifier (missed_meal, anomaly_spike, occlusion_cluster, etc.).';
COMMENT ON COLUMN detection_results.anchor_timestamp IS
    'The data point in the time-series this detection is anchored to (meal start, spike apex, alarm time, ...).';
COMMENT ON COLUMN detection_results.payload IS
    'JSON-shaped per-detection details. Schema is owned by the producing detector.';
COMMENT ON COLUMN detection_results.created_at IS
    'Wall-clock when the detection ran. Used as the time-window filter for SupabaseStorage.list_detection_results(since=...).';
COMMENT ON INDEX detection_results_kind_created_idx IS
    'Supports the dominant query SupabaseStorage.list_detection_results(kind=..., since=...) — covers the WHERE kind = $1 AND created_at >= $2 ORDER BY created_at DESC pattern.';
