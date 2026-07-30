-- Separate database for LiteLLM proxy (Admin UI / virtual keys).
-- Runs only on first Postgres volume init.
CREATE DATABASE litellm;
-- POSTGRES_USER is intentionally configurable in production. The entrypoint
-- invokes this file as that bootstrap role, so grant to the actual current
-- user instead of assuming the development-only "ontoprompt" role exists.
SELECT format(
    'GRANT ALL PRIVILEGES ON DATABASE litellm TO %I',
    current_user
) \gexec
