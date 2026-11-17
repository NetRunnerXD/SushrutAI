-- SushrutAI application role. The API must NOT connect as a superuser: superusers bypass row-level
-- security even when it is FORCEd. sushrut_app owns the schema/tables (FORCE RLS applies to owners).
-- Change the password in production and pass it via DATABASE_URL.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sushrut_app') THEN
    CREATE ROLE sushrut_app LOGIN PASSWORD 'sushrut_app_dev_only' NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB;
  END IF;
END $$;
ALTER SCHEMA public OWNER TO sushrut_app;
CREATE EXTENSION IF NOT EXISTS vector;
