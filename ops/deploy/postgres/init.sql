-- ----------------------------------------------------------------------------
-- JARVIS — bootstrap de Postgres (correr UNA SOLA VEZ).
--
-- Cómo correr:
--     sudo -u postgres psql -f init.sql
--
-- Crea:
--   * Base `jarvis` (la usa el orquestador para sus propias tablas).
--   * Rol `jarvis`        — owner de la base `jarvis`. App del orquestador.
--   * Rol `jarvis_ro`     — solo lectura. Lo usan los sub-agentes para
--                           consultar lar/icemm/atempora.
--
-- DESPUÉS hay que dar `GRANT SELECT` a `jarvis_ro` sobre las tablas
-- relevantes en lar/icemm/atempora (al final del archivo hay un template).
-- ----------------------------------------------------------------------------

-- Rol del orquestador (read-write sobre la DB jarvis solamente)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jarvis') THEN
        CREATE ROLE jarvis LOGIN PASSWORD 'CHANGE_ME_jarvis_pwd';
    END IF;
END
$$;

-- Rol read-only para los datos de negocio
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jarvis_ro') THEN
        CREATE ROLE jarvis_ro LOGIN PASSWORD 'CHANGE_ME_jarvis_ro_pwd';
    END IF;
END
$$;

-- Base de datos del orquestador
SELECT 'CREATE DATABASE jarvis OWNER jarvis'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'jarvis')
\gexec

-- ----------------------------------------------------------------------------
-- TEMPLATE: dar SELECT a jarvis_ro sobre tablas existentes en lar.
-- Adaptar según las bases reales (lar / icemm / atempora) y schemas.
-- ----------------------------------------------------------------------------
-- \c lar
-- GRANT CONNECT ON DATABASE lar TO jarvis_ro;
-- GRANT USAGE ON SCHEMA public TO jarvis_ro;
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO jarvis_ro;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO jarvis_ro;
--
-- \c icemm
-- GRANT CONNECT ON DATABASE icemm TO jarvis_ro;
-- GRANT USAGE ON SCHEMA public TO jarvis_ro;
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO jarvis_ro;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO jarvis_ro;
--
-- \c atempora
-- GRANT CONNECT ON DATABASE atempora TO jarvis_ro;
-- GRANT USAGE ON SCHEMA public TO jarvis_ro;
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO jarvis_ro;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO jarvis_ro;
