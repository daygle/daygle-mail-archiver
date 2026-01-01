--
-- Daygle Mail Archiver – Database Schema
-- This file is executed automatically on first run by the Postgres container.
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
-- Settings table for runtime configuration
-------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Initial defaults (can be overridden via Settings UI)
INSERT INTO settings (key, value)
VALUES
    -- IMAP
    ('imap_host', 'imap.example.com'),
    ('imap_port', '993'),
    ('imap_user', 'user@example.com'),
    ('imap_use_ssl', 'true'),
    ('imap_require_starttls', 'false'),
    ('imap_ca_bundle', ''),
    -- IMAP password is stored encrypted by the app; leave empty by default
    ('imap_password_encrypted', ''),
    -- Worker
    ('poll_interval_seconds', '300'),
    ('delete_after_processing', 'true'),
    -- Storage
    ('storage_dir', '/data/mail'),
    -- UI
    ('page_size', '50')
ON CONFLICT (key) DO NOTHING;

-------------------------------------------------------------------
-- Worker status heartbeat
-------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS worker_status (
    id INTEGER PRIMARY KEY,
    last_heartbeat TIMESTAMP,
    last_success TIMESTAMP,
    last_error TEXT,
    last_run_duration_seconds INTEGER,
    messages_processed INTEGER DEFAULT 0
);

-- Ensure a single row with id = 1 exists
INSERT INTO worker_status (id, last_heartbeat, last_success, last_error, last_run_duration_seconds, messages_processed)
VALUES (1, NULL, NULL, NULL, NULL, 0)
ON CONFLICT (id) DO NOTHING;