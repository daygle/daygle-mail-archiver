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

    -- Virus scanning results
    virus_scanned BOOLEAN NOT NULL DEFAULT FALSE,
    virus_detected BOOLEAN NOT NULL DEFAULT FALSE,
    virus_name TEXT,
    scan_timestamp TIMESTAMPTZ,

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

-- Index for date range queries and filtering
CREATE INDEX IF NOT EXISTS emails_created_at_idx ON emails(created_at DESC);
CREATE INDEX IF NOT EXISTS emails_source_idx ON emails(source);
CREATE INDEX IF NOT EXISTS emails_sender_idx ON emails(sender);
CREATE INDEX IF NOT EXISTS emails_virus_detected_idx ON emails(virus_detected) WHERE virus_detected = TRUE;

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
    last_error TEXT,
    
    CHECK (account_type IN ('imap', 'gmail', 'o365'))
);

-- Index on enabled accounts for worker queries
CREATE INDEX IF NOT EXISTS fetch_accounts_enabled_idx ON fetch_accounts(enabled) WHERE enabled = TRUE;

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
    password_hash TEXT NOT NULL DEFAULT '',  -- Empty string for unset passwords
    first_name TEXT,
    last_name TEXT,
    email TEXT,
    role TEXT NOT NULL DEFAULT 'administrator',
    page_size INTEGER NOT NULL DEFAULT 50,
    date_format TEXT NOT NULL DEFAULT '%d/%m/%Y',
    time_format TEXT NOT NULL DEFAULT '%H:%M',
    timezone TEXT NOT NULL DEFAULT 'Australia/Melbourne',
    theme_preference TEXT NOT NULL DEFAULT 'system',
    email_notifications BOOLEAN NOT NULL DEFAULT TRUE,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    last_login TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index on email for lookups
CREATE INDEX IF NOT EXISTS users_email_idx ON users(email);
CREATE INDEX IF NOT EXISTS users_role_idx ON users(role);

-- Default administrator user will be created during initial setup wizard

-- ----------------------------
-- settings
-- ----------------------------
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Insert default settings
INSERT INTO settings (key, value) VALUES ('page_size', '50') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('date_format', '%d/%m/%Y') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('time_format', '%H:%M') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('timezone', 'Australia/Melbourne') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('default_theme', 'system') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('enable_update_check', 'true') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('update_check_ttl', '600') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('enable_purge', 'false') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('retention_value', '1') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('retention_unit', 'years') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('retention_delete_from_mail_server', 'false') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('setup_complete', 'false') ON CONFLICT (key) DO NOTHING;
-- ClamAV settings
INSERT INTO settings (key, value) VALUES ('clamav_enabled', 'true') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('clamav_host', 'clamav') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('clamav_port', '3310') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('clamav_action', 'quarantine') ON CONFLICT (key) DO NOTHING;
-- SMTP settings for email alerts
INSERT INTO settings (key, value) VALUES ('smtp_enabled', 'false') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('smtp_host', '') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('smtp_port', '587') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('smtp_username', '') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('smtp_password', '') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('smtp_use_tls', 'true') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('smtp_from_email', '') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('smtp_from_name', 'Daygle Mail Archiver') ON CONFLICT (key) DO NOTHING;

-- ----------------------------
-- logs
-- ----------------------------
CREATE TABLE IF NOT EXISTS logs (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    level TEXT NOT NULL DEFAULT 'info',
    source TEXT,
    message TEXT NOT NULL,
    details TEXT
);

-- Indexes for logs queries (filtering by level and ordering by timestamp)
CREATE INDEX IF NOT EXISTS logs_timestamp_idx ON logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS logs_level_timestamp_idx ON logs(level, timestamp DESC);

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

-- ----------------------------
-- dashboard_preferences
-- Stores user dashboard widget layout preferences
-- ----------------------------
CREATE TABLE IF NOT EXISTS dashboard_preferences (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    widget_id TEXT NOT NULL,
    x_position INTEGER NOT NULL,
    y_position INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    is_visible BOOLEAN NOT NULL DEFAULT TRUE,
    
    UNIQUE (user_id, widget_id)
);

-- Index for quick lookups by user
CREATE INDEX IF NOT EXISTS dashboard_preferences_user_idx ON dashboard_preferences(user_id);

-- ----------------------------
-- user_widget_settings
-- Stores user widget configuration settings (e.g., days range for charts)
-- ----------------------------
CREATE TABLE IF NOT EXISTS user_widget_settings (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    settings JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for quick lookups by user
CREATE INDEX IF NOT EXISTS user_widget_settings_user_idx ON user_widget_settings(user_id);

-- ----------------------------
-- alerts
-- System alerts and notifications
-- ----------------------------
CREATE TABLE IF NOT EXISTS alerts (
    id SERIAL PRIMARY KEY,
    alert_type TEXT NOT NULL, -- 'error', 'warning', 'info', 'success'
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    details TEXT,
    acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
    email_sent BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acknowledged_at TIMESTAMPTZ,
    acknowledged_by INTEGER REFERENCES users(id)
);

-- Index for filtering by type and status
CREATE INDEX IF NOT EXISTS alerts_type_idx ON alerts(alert_type);
CREATE INDEX IF NOT EXISTS alerts_acknowledged_idx ON alerts(acknowledged);
CREATE INDEX IF NOT EXISTS alerts_created_at_idx ON alerts(created_at DESC);

-- ----------------------------
-- alert_triggers
-- Defines available alert triggers that can be enabled/disabled
-- ----------------------------
CREATE TABLE IF NOT EXISTS alert_triggers (
    id SERIAL PRIMARY KEY,
    trigger_key TEXT UNIQUE NOT NULL, -- Unique identifier like 'virus_detected', 'clamav_error'
    name TEXT NOT NULL, -- Human-readable name like 'Virus Detected'
    description TEXT, -- Description of what this alert is for
    alert_type TEXT NOT NULL DEFAULT 'warning', -- Default severity: 'error', 'warning', 'info', 'success'
    enabled BOOLEAN NOT NULL DEFAULT TRUE, -- Whether this trigger is enabled globally
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for quick lookups
CREATE INDEX IF NOT EXISTS alert_triggers_enabled_idx ON alert_triggers(enabled);
CREATE INDEX IF NOT EXISTS alert_triggers_key_idx ON alert_triggers(trigger_key);

-- Insert default alert triggers
INSERT INTO alert_triggers (trigger_key, name, description, alert_type, enabled) VALUES
    ('virus_detected', 'Virus Detected', 'Alert when a virus is detected in an email', 'error', TRUE),
    ('clamav_error', 'ClamAV Error', 'Alert when ClamAV service encounters an error', 'error', TRUE),
    ('clamav_unavailable', 'ClamAV Unavailable', 'Alert when ClamAV service is unavailable', 'warning', TRUE),
    ('clamav_config_error', 'ClamAV Configuration Error', 'Alert when ClamAV configuration cannot be loaded', 'error', TRUE),
    ('low_disk_space', 'Low Disk Space', 'Alert when disk space is running low', 'warning', TRUE),
    ('worker_error', 'Worker Error', 'Alert when email worker encounters an error', 'error', TRUE),
    ('account_sync_error', 'Account Sync Error', 'Alert when email account synchronization fails', 'error', TRUE),
    ('smtp_error', 'SMTP Error', 'Alert when email sending fails', 'warning', TRUE),
    ('email_processed', 'Email Processed Successfully', 'Alert when an email is successfully processed and archived', 'success', FALSE),
    ('backup_completed', 'Backup Completed', 'Alert when a system backup completes successfully', 'success', FALSE),
    ('system_startup', 'System Startup', 'Alert when the system starts up successfully', 'info', FALSE),
    ('maintenance_mode', 'Maintenance Mode', 'Alert when maintenance mode is enabled or disabled', 'info', TRUE)
ON CONFLICT (trigger_key) DO UPDATE SET
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    alert_type = EXCLUDED.alert_type,
    enabled = EXCLUDED.enabled;
