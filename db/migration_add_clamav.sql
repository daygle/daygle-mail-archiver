-- ============================================
-- Migration: Add ClamAV support
-- ============================================
-- This migration adds virus scanning fields to emails table
-- and ClamAV settings to the settings table

-- Add virus scanning columns to emails table
ALTER TABLE emails ADD COLUMN IF NOT EXISTS virus_scanned BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE emails ADD COLUMN IF NOT EXISTS virus_detected BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE emails ADD COLUMN IF NOT EXISTS virus_name TEXT;
ALTER TABLE emails ADD COLUMN IF NOT EXISTS scan_timestamp TIMESTAMPTZ;

-- Add index for querying infected emails
CREATE INDEX IF NOT EXISTS emails_virus_detected_idx ON emails(virus_detected) WHERE virus_detected = TRUE;

-- Add ClamAV settings
INSERT INTO settings (key, value) VALUES ('clamav_enabled', 'true') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('clamav_host', 'clamav') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('clamav_port', '3310') ON CONFLICT (key) DO NOTHING;
INSERT INTO settings (key, value) VALUES ('clamav_action', 'quarantine') ON CONFLICT (key) DO NOTHING;
