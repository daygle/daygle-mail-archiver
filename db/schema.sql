-- ============================================
-- Daygle Mail Archiver - Database Schema
-- ============================================

-- ----------------------------
-- messages
-- ----------------------------
CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,

    -- IMAP source info
    source TEXT NOT NULL,
    folder TEXT NOT NULL,
    uid INTEGER NOT NULL,

    -- Metadata
    subject TEXT,
    sender TEXT,
    recipients TEXT,
    date TEXT,

    -- Raw email storage
    raw_email BYTEA NOT NULL,
    compressed BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (source, folder, uid)
);

-- Full-text search index
CREATE INDEX IF NOT EXISTS messages_fts_idx
ON messages
USING GIN (
    to_tsvector(
        'simple',
        coalesce(subject, '') || ' ' ||
        coalesce(sender, '') || ' ' ||
        coalesce(recipients, '')
    )
);

-- ----------------------------
-- imap_accounts
-- ----------------------------
CREATE TABLE IF NOT EXISTS imap_accounts (
    id SERIAL PRIMARY KEY,

    name TEXT NOT NULL UNIQUE,

    host TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 993,
    username TEXT NOT NULL,
    password_encrypted TEXT NOT NULL,

    use_ssl BOOLEAN NOT NULL DEFAULT TRUE,
    require_starttls BOOLEAN NOT NULL DEFAULT FALSE,

    poll_interval_seconds INTEGER NOT NULL DEFAULT 300,
    delete_after_processing BOOLEAN NOT NULL DEFAULT FALSE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,

    last_heartbeat TIMESTAMPTZ,
    last_success TIMESTAMPTZ,
    last_error TEXT
);

-- ----------------------------
-- imap_state
-- Tracks last UID per folder per account
-- ----------------------------
CREATE TABLE IF NOT EXISTS imap_state (
    account_id INTEGER NOT NULL REFERENCES imap_accounts(id) ON DELETE CASCADE,
    folder TEXT NOT NULL,
    last_uid INTEGER NOT NULL DEFAULT 0,

    PRIMARY KEY (account_id, folder)
);

-- ----------------------------
-- users
-- ----------------------------
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Insert default admin user if not exists
INSERT INTO users (username, password_hash)
VALUES ('administrator', '$2b$12$abcdefghijklmnopqrstuvwx')  -- Replace with actual bcrypt hash for 'administrator'
ON CONFLICT (username) DO NOTHING;

-- ----------------------------
-- settings
-- ----------------------------
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- ----------------------------
-- error_log
-- ----------------------------
CREATE TABLE IF NOT EXISTS error_log (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source TEXT,
    message TEXT NOT NULL,
    details TEXT
);