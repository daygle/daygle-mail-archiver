-- Migration: Add user_widget_settings table for storing widget configuration
-- This adds support for configurable day ranges in dashboard chart widgets

-- Create the user_widget_settings table if it doesn't exist
CREATE TABLE IF NOT EXISTS user_widget_settings (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    settings JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for quick lookups by user
CREATE INDEX IF NOT EXISTS user_widget_settings_user_idx ON user_widget_settings(user_id);

-- Log migration completion
DO $$
BEGIN
    RAISE NOTICE 'Migration completed: user_widget_settings table created';
END $$;
