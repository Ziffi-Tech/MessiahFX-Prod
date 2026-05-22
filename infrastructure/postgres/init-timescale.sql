-- ─────────────────────────────────────────────────────────────────────────────
-- MeznaQuantFX AI — PostgreSQL + TimescaleDB Initialisation
-- This runs once when the database container is first created.
-- Actual schema is managed by Alembic migrations.
-- ─────────────────────────────────────────────────────────────────────────────

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Verify TimescaleDB is active
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'
    ) THEN
        RAISE EXCEPTION 'TimescaleDB extension failed to load';
    END IF;
    RAISE NOTICE 'TimescaleDB initialised successfully';
END;
$$;
