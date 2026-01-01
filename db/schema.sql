--
-- Daygle Mail Archiver – Database Schema (Updated)
-- Clean, modern, per‑account IMAP architecture
--

-- Needed for password hashing (crypt/gen_salt)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-------------------------------------------------------------------
-- Messages table
-------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,

    -- IMAP metadata
    account TEXT NOT NULL,
    folder TEXT NOT NULL,
    uid BIGINT NOT NULL,

    -- Email metadata
    subject TEXT,
    sender TEXT,
    recipients TEXT,
    date TIMESTAMP,
    storage_path TEXT NOT NULL,

    -- Internal metadata
    created_at TIMESTAMP DEFAULT NOW(),

    -- Full-text search vector
    search_vector tsvector
);

-- Ensure uniqueness of messages per account/folder/uid
CREATE UNIQUE INDEX IF NOT EXISTS messages_unique_idx
    ON messages (account, folder, uid);

-- Full-text search trigger function
CREATE OR REPLACE FUNCTION messages_search_vector_update()
RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        to_tsvector(
            'simple',
            coalesce(NEW.subject, '') || ' ' ||
            coalesce(NEW.sender, '') || ' ' ||
            coalesce(NEW.recipients, '')
        );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to maintain search_vector on insert/update
CREATE TRIGGER messages_search_vector_trigger
BEFORE INSERT OR UPDATE ON messages
FOR EACH ROW EXECUTE FUNCTION messages_search_vector_update();

-- GIN index for fast full-text search
CREATE INDEX IF NOT EXISTS messages_search_vector_idx
    ON messages
    USING GIN (search_vector);

-------------------------------------------------------------------
-- Users table for admin login
-------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Create default administrator user (username: administrator, password: administrator)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM users WHERE username = 'administrator') THEN
        INSERT INTO users (username, password_hash)
        VALUES ('administrator', crypt('administrator', gen_salt('bf')));
    END IF;
END;
$$;

-------------------------------------------------------------------
-- Settings table for runtime configuration (global)
-------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Initial defaults (only true global settings)
INSERT INTO settings (key, value)
VALUES
    ('storage_dir', '/data/mail'),
    ('page_size', '50')
ON CONFLICT (key) DO NOTHING;

-------------------------------------------------------------------
-- IMAP accounts (per-account configuration)
-------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS imap_accounts (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,               -- short, human-friendly, used in logs/labels
    host TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 993,
    username TEXT NOT NULL,
    password_encrypted TEXT NOT NULL DEFAULT '',
    use_ssl BOOLEAN NOT NULL DEFAULT TRUE,
    require_starttls BOOLEAN NOT NULL DEFAULT FALSE,
    ca_bundle TEXT NOT NULL DEFAULT '',
    poll_interval_seconds INTEGER NOT NULL DEFAULT 300,
    delete_after_processing BOOLEAN NOT NULL DEFAULT TRUE,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,  -- IMPORTANT: default disabled
    last_heartbeat TIMESTAMP,
    last_success TIMESTAMP,
    last_error TEXT
);

-- Optional seed account (disabled by default)
INSERT INTO imap_accounts (name, host, port, username, use_ssl, enabled)
VALUES ('default', 'imap.example.com', 993, 'user@example.com', TRUE, FALSE)
ON CONFLICT (name) DO NOTHING;

-------------------------------------------------------------------
-- Error log (recent errors view)
-------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS error_log (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    source TEXT NOT NULL,         -- e.g., 'worker:default', 'api', 'storage', 'db'
    message TEXT NOT NULL,
    details TEXT
);

CREATE INDEX IF NOT EXISTS error_log_timestamp_idx
    ON error_log (timestamp DESC);