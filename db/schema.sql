-- ============================================
-- Daygle Mail Archiver - Database Schema
-- ============================================

-- ----------------------------
-- emails
-- ----------------------------
CREATE TABLE IF NOT EXISTS emails (
    id SERIAL PRIMARY KEY,

    -- Source
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
CREATE INDEX IF NOT EXISTS emails_fts_idx
ON emails
USING GIN (
    to_tsvector(
        'simple',
        coalesce(subject, '') || ' ' ||
        coalesce(sender, '') || ' ' ||
        coalesce(recipients, '')
    )
);

-- ----------------------------
-- fetch_accounts
-- Supports multiple email fetch methods: IMAP, Gmail API, O365 API
-- ----------------------------
CREATE TABLE IF NOT EXISTS fetch_accounts (
    id SERIAL PRIMARY KEY,

    name TEXT NOT NULL UNIQUE,
    account_type TEXT NOT NULL DEFAULT 'imap',

    -- IMAP-specific fields
    host TEXT,
    port INTEGER DEFAULT 993,
    username TEXT,
    password_encrypted TEXT,
    use_ssl BOOLEAN DEFAULT TRUE,
    require_starttls BOOLEAN DEFAULT FALSE,

    -- OAuth2 fields (for Gmail/O365)
    oauth_client_id TEXT,
    oauth_client_secret TEXT,
    oauth_refresh_token TEXT,
    oauth_access_token TEXT,
    oauth_token_expiry TIMESTAMPTZ,

    -- Common fields
    poll_interval_seconds INTEGER NOT NULL DEFAULT 300,
    delete_after_processing BOOLEAN NOT NULL DEFAULT FALSE,
    expunge_deleted BOOLEAN NOT NULL DEFAULT FALSE, -- IMAP only: permanently expunge deleted messages
    enabled BOOLEAN NOT NULL DEFAULT TRUE,

    last_heartbeat TIMESTAMPTZ,
    last_success TIMESTAMPTZ,
    last_error TEXT
);

-- ----------------------------
-- fetch_state
-- Tracks last UID/token per folder per account
-- ----------------------------
CREATE TABLE IF NOT EXISTS fetch_state (
    account_id INTEGER NOT NULL REFERENCES fetch_accounts(id) ON DELETE CASCADE,
    folder TEXT NOT NULL,
    last_uid INTEGER NOT NULL DEFAULT 0,
    last_sync_token TEXT, -- For Gmail/O365 sync tokens

    PRIMARY KEY (account_id, folder)
);

-- ----------------------------
-- users
-- ----------------------------
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    first_name TEXT,
    last_name TEXT,
    email TEXT,
    role TEXT NOT NULL DEFAULT 'administrator',
    date_format TEXT NOT NULL DEFAULT '%d/%m/%Y %H:%M',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    last_login TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index on email for lookups
CREATE INDEX IF NOT EXISTS users_email_idx ON users(email);
CREATE INDEX IF NOT EXISTS users_role_idx ON users(role);

-- Insert default admin user with no password (set on first login)
INSERT INTO users (username, password_hash, role)
VALUES ('administrator', '', 'administrator')
ON CONFLICT (username) DO NOTHING;

-- ----------------------------
-- settings
-- ----------------------------
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Insert default settings
INSERT INTO settings (key, value) VALUES ('page_size', '50') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('date_format', '%d/%m/%Y %H:%M') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('enable_purge', 'false') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('retention_value', '1') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('retention_unit', 'years') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('retention_delete_from_mail_server', 'false') ON CONFLICT (key) DO NOTHING;

-- ----------------------------
-- logs
-- ----------------------------
CREATE TABLE IF NOT EXISTS logs (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    level TEXT NOT NULL DEFAULT 'error',
    source TEXT,
    message TEXT NOT NULL,
    details TEXT
);

-- ----------------------------
-- deletion_stats
-- Tracks email deletions for dashboard analytics
-- ----------------------------
CREATE TABLE IF NOT EXISTS deletion_stats (
    id SERIAL PRIMARY KEY,
    deletion_date DATE NOT NULL DEFAULT CURRENT_DATE,
    deletion_type TEXT NOT NULL, -- 'manual' or 'retention'
    count INTEGER NOT NULL DEFAULT 0,
    deleted_from_mail_server BOOLEAN NOT NULL DEFAULT FALSE,
    
    UNIQUE (deletion_date, deletion_type, deleted_from_mail_server)
);