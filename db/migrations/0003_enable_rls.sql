-- =============================================================================
-- t1d-engine migration 0003 -- enable Row-Level Security
-- =============================================================================
--
-- Enables RLS on every public table and grants a single permissive
-- `FOR ALL TO authenticated` policy per table. Locks the project down ahead
-- of the dashboard work that will introduce Supabase Auth + anon-key client
-- bundles.
--
-- Threat model (four Supabase roles):
--
--   1. postgres        -- BYPASSRLS. Used by `bootstrap_supabase.py`, the
--                          GitHub Action nightly sync, and `SupabaseStorage`
--                          via psycopg2. Unaffected by this migration.
--   2. service_role    -- BYPASSRLS. Used by future Vercel API routes that
--                          need admin access via the service-role JWT.
--                          Unaffected.
--   3. authenticated   -- Subject to RLS. The Supabase JWT role for signed-in
--                          users. The policy below grants full read/write on
--                          every public table (single-user project).
--   4. anon            -- Subject to RLS. The Supabase JWT role for
--                          unauthenticated requests (the key embedded in
--                          client bundles). Gets NO policy after this
--                          migration -- sees zero rows on every public table.
--
-- Policy decision: one permissive `auth_required_all` policy per table that
-- matches `FOR ALL TO authenticated USING (true) WITH CHECK (true)`. This is
-- the minimum-viable lockdown: it closes the anon-key data-leak vector and
-- removes the `rls_disabled` advisor warning without breaking any existing
-- code path (postgres + service_role bypass RLS). Tightening to per-row
-- ownership (`USING (user_id = auth.uid())`) is deferred until the project
-- grows past its single-user shape.
--
-- Idempotency: `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` is naturally a
-- no-op when RLS is already on. `CREATE POLICY` is NOT idempotent in
-- Postgres (no `IF NOT EXISTS` variant), so each policy is wrapped in a
-- `DO/EXCEPTION duplicate_object` block -- the same convention used for
-- enums in `0001_init.sql`.
--
-- Tables covered (13 total):
--   9 data tables:     cgm, bolus, requests, basal, suspension, events,
--                      alarms, site_issues, cgm_gaps
--   4 metadata tables: alerts_sent, fetch_state, detection_config,
--                      detection_results
--
-- See `docs/plans/2026-05-17-enable-rls.md` for the full design discussion.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- cgm
-- -----------------------------------------------------------------------------
ALTER TABLE cgm ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY auth_required_all ON cgm
        FOR ALL TO authenticated
        USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- -----------------------------------------------------------------------------
-- bolus
-- -----------------------------------------------------------------------------
ALTER TABLE bolus ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY auth_required_all ON bolus
        FOR ALL TO authenticated
        USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- -----------------------------------------------------------------------------
-- requests
-- -----------------------------------------------------------------------------
ALTER TABLE requests ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY auth_required_all ON requests
        FOR ALL TO authenticated
        USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- -----------------------------------------------------------------------------
-- basal
-- -----------------------------------------------------------------------------
ALTER TABLE basal ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY auth_required_all ON basal
        FOR ALL TO authenticated
        USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- -----------------------------------------------------------------------------
-- suspension
-- -----------------------------------------------------------------------------
ALTER TABLE suspension ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY auth_required_all ON suspension
        FOR ALL TO authenticated
        USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- -----------------------------------------------------------------------------
-- events
-- -----------------------------------------------------------------------------
ALTER TABLE events ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY auth_required_all ON events
        FOR ALL TO authenticated
        USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- -----------------------------------------------------------------------------
-- alarms
-- -----------------------------------------------------------------------------
ALTER TABLE alarms ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY auth_required_all ON alarms
        FOR ALL TO authenticated
        USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- -----------------------------------------------------------------------------
-- site_issues
-- -----------------------------------------------------------------------------
ALTER TABLE site_issues ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY auth_required_all ON site_issues
        FOR ALL TO authenticated
        USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- -----------------------------------------------------------------------------
-- cgm_gaps
-- -----------------------------------------------------------------------------
ALTER TABLE cgm_gaps ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY auth_required_all ON cgm_gaps
        FOR ALL TO authenticated
        USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- -----------------------------------------------------------------------------
-- alerts_sent
-- -----------------------------------------------------------------------------
ALTER TABLE alerts_sent ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY auth_required_all ON alerts_sent
        FOR ALL TO authenticated
        USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- -----------------------------------------------------------------------------
-- fetch_state
-- -----------------------------------------------------------------------------
ALTER TABLE fetch_state ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY auth_required_all ON fetch_state
        FOR ALL TO authenticated
        USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- -----------------------------------------------------------------------------
-- detection_config
-- -----------------------------------------------------------------------------
ALTER TABLE detection_config ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY auth_required_all ON detection_config
        FOR ALL TO authenticated
        USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- -----------------------------------------------------------------------------
-- detection_results
-- -----------------------------------------------------------------------------
ALTER TABLE detection_results ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY auth_required_all ON detection_results
        FOR ALL TO authenticated
        USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
