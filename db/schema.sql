--
-- SETTINGS TABLE
--
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);


--
-- IMAP ACCOUNTS
--
CREATE TABLE imap_accounts (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 993,
    username TEXT NOT NULL,
    password_encrypted TEXT NOT NULL,

    use_ssl BOOLEAN NOT NULL DEFAULT TRUE,
    require_starttls BOOLEAN NOT NULL DEFAULT FALSE,

    poll_interval_seconds INTEGER NOT NULL DEFAULT 300,
    delete_after_processing BOOLEAN NOT NULL DEFAULT FALSE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,

    ca_bundle TEXT,

    last_heartbeat TIMESTAMPTZ,
    last_success TIMESTAMPTZ,
    last_error TEXT
);

CREATE INDEX idx_imap_accounts_enabled ON imap_accounts(enabled);


--
-- MESSAGES TABLE
-- Stores compressed raw email (RFC822) directly in the DB.
--
CREATE TABLE messages (
    id SERIAL PRIMARY KEY,

    source TEXT NOT NULL,
    folder TEXT NOT NULL,
    uid TEXT NOT NULL,

    subject TEXT,
    sender TEXT,
    recipients TEXT,

    date TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    raw_email BYTEA NOT NULL,
    compressed BOOLEAN NOT NULL DEFAULT TRUE
);

-- Useful indexes
CREATE INDEX idx_messages_source ON messages(source);
CREATE INDEX idx_messages_folder ON messages(folder);
CREATE INDEX idx_messages_date ON messages(date);
CREATE INDEX idx_messages_created_at ON messages(created_at);

-- Full‑text search index (simplified FTS)
CREATE INDEX idx_messages_fts ON messages
USING GIN (
    to_tsvector(
        'simple',
        coalesce(subject, '') || ' ' ||
        coalesce(sender, '') || ' ' ||
        coalesce(recipients, '')
    )
);


--
-- ERROR LOG
--
CREATE TABLE error_log (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source TEXT,
    message TEXT NOT NULL,
    details TEXT
);

CREATE INDEX idx_error_log_timestamp ON error_log(timestamp DESC);