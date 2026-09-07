-- ============================================================================
-- Picker Hunt — Neon (Postgres) schema
-- ============================================================================
-- Run this once against a fresh Neon project — either in the Neon SQL editor
-- (Console → your project → SQL Editor) or via `psql "$DATABASE_URL" -f neon_schema.sql`.
-- It creates every table used by database.py.
--
-- NOTE ON SEED DATA: you do NOT need to insert admin users here. main.py
-- calls seed_admin() on every app startup, which creates (idempotently):
--   - username=admin    password=admin123  store=929   (or $ADMIN_PASSWORD)
--   - username=admin96  password=admin96   store=96    (or $ADMIN96_PASSWORD)
-- Just point DATABASE_URL at this Neon project and start the app once.
-- ============================================================================

-- ── users ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,
    username        VARCHAR(50)  NOT NULL UNIQUE,
    email           VARCHAR(200),
    password_hash   VARCHAR(200) NOT NULL,
    role            VARCHAR(20)  NOT NULL,          -- admin | buscador | shopper | subgerente
    status          VARCHAR(10)  DEFAULT 'activo',  -- activo | inactivo
    store           VARCHAR(10)  DEFAULT '929',      -- '929' | '96'
    created_at      TIMESTAMPTZ  DEFAULT now(),
    reset_token     VARCHAR(100),
    reset_expires   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_users_email        ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_reset_token  ON users(reset_token);
CREATE INDEX IF NOT EXISTS idx_users_store        ON users(store);

-- ── items (active picking requests) ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS items (
    id                  SERIAL PRIMARY KEY,
    code                VARCHAR(7)  NOT NULL,
    description         TEXT        NOT NULL,
    quantity            INTEGER     NOT NULL,
    status              VARCHAR(20) DEFAULT 'pendiente',  -- pendiente | encontrado | en_sala | no_encontrado | eliminado | cancelado
    created_at          TIMESTAMPTZ DEFAULT now(),
    expires_at          TIMESTAMPTZ NOT NULL,
    created_by_id       INTEGER     REFERENCES users(id),
    responded_by_id     INTEGER     REFERENCES users(id),
    comment             TEXT,
    shipping_info       TEXT,
    confirmed_by_id     INTEGER     REFERENCES users(id),
    image_data          TEXT,                              -- base64-encoded image
    image_mime          VARCHAR(50),
    claimed_by_id       INTEGER     REFERENCES users(id),
    claimed_at          TIMESTAMPTZ,
    no_encontrado_at    TIMESTAMPTZ,
    responded_at        TIMESTAMPTZ,
    store               VARCHAR(10) DEFAULT '929'
);
CREATE INDEX IF NOT EXISTS idx_items_code    ON items(code);
CREATE INDEX IF NOT EXISTS idx_items_store   ON items(store);          -- every dashboard query filters by store
CREATE INDEX IF NOT EXISTS idx_items_status  ON items(status);

-- ── item_history (closed/archived items) ────────────────────────────────
CREATE TABLE IF NOT EXISTS item_history (
    id                  SERIAL PRIMARY KEY,
    item_code           VARCHAR(7)  NOT NULL,
    description         TEXT        NOT NULL,
    quantity            INTEGER     NOT NULL,
    status              VARCHAR(20) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL,
    closed_at           TIMESTAMPTZ DEFAULT now(),
    time_used_seconds   INTEGER,
    responded_by        VARCHAR(100),
    responded_by_id     INTEGER     REFERENCES users(id),
    comment             TEXT,
    shipping_info       TEXT,
    confirmed_by        VARCHAR(100),
    created_by          VARCHAR(100)
);
CREATE INDEX IF NOT EXISTS idx_item_history_closed_at ON item_history(closed_at);
CREATE INDEX IF NOT EXISTS idx_item_history_status    ON item_history(status);

-- ── tareas (subgerente task planning) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS tareas (
    id                      SERIAL PRIMARY KEY,
    descripcion             TEXT        NOT NULL,
    estado                  VARCHAR(20) DEFAULT 'pendiente',  -- pendiente | en_progreso | completada
    creado_en               TIMESTAMPTZ DEFAULT now(),
    fecha_planificacion     VARCHAR(10),                       -- YYYY-MM-DD
    creado_por_id           INTEGER     REFERENCES users(id),
    image_data              TEXT,
    image_mime              VARCHAR(50)
);

-- ── checklist_locales ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS checklist_locales (
    id      SERIAL PRIMARY KEY,
    nombre  VARCHAR(100) NOT NULL UNIQUE,
    activo  VARCHAR(5)   DEFAULT 'si'  -- si | no
);

-- ── checklist_secciones ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS checklist_secciones (
    id        SERIAL PRIMARY KEY,
    local_id  INTEGER      NOT NULL REFERENCES checklist_locales(id) ON DELETE CASCADE,
    nombre    VARCHAR(100) NOT NULL,
    activo    VARCHAR(5)   DEFAULT 'si'
);
CREATE INDEX IF NOT EXISTS idx_checklist_secciones_local_id ON checklist_secciones(local_id);

-- ── checklist_rutinas (catalog, can be section-scoped or global) ────────
CREATE TABLE IF NOT EXISTS checklist_rutinas (
    id            SERIAL PRIMARY KEY,
    descripcion   TEXT         NOT NULL,
    categoria     VARCHAR(100),
    orden         INTEGER      DEFAULT 0,
    activo        VARCHAR(5)   DEFAULT 'si'
);

-- ── checklist_sesiones (one checklist run: local + date + section + shift) ──
CREATE TABLE IF NOT EXISTS checklist_sesiones (
    id              SERIAL PRIMARY KEY,
    local_id        INTEGER     NOT NULL REFERENCES checklist_locales(id),
    seccion_id      INTEGER     REFERENCES checklist_secciones(id),
    fecha           VARCHAR(10) NOT NULL,   -- YYYY-MM-DD
    turno           VARCHAR(10) NOT NULL,   -- AM | PM | Noche
    creado_por_id   INTEGER     REFERENCES users(id),
    creado_en       TIMESTAMPTZ DEFAULT now(),
    cerrado         VARCHAR(5)  DEFAULT 'no'
);
CREATE INDEX IF NOT EXISTS idx_checklist_sesiones_local_id  ON checklist_sesiones(local_id);
CREATE INDEX IF NOT EXISTS idx_checklist_sesiones_fecha     ON checklist_sesiones(fecha);

-- ── checklist_respuestas (answer to one routine inside a session) ──────
CREATE TABLE IF NOT EXISTS checklist_respuestas (
    id              SERIAL PRIMARY KEY,
    sesion_id       INTEGER     NOT NULL REFERENCES checklist_sesiones(id) ON DELETE CASCADE,
    rutina_id       INTEGER     NOT NULL REFERENCES checklist_rutinas(id),
    resultado       VARCHAR(15) DEFAULT 'pendiente',  -- pendiente | cumple | no_cumple
    observacion     TEXT,
    respondido_en   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_checklist_respuestas_sesion_id ON checklist_respuestas(sesion_id);
CREATE INDEX IF NOT EXISTS idx_checklist_respuestas_rutina_id ON checklist_respuestas(rutina_id);
