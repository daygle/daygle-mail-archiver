#!/usr/bin/env python3
"""
Daygle Mail Archiver - Database Initialisation
Initialise the database with required tables for development.
"""

import os
import sys
from pathlib import Path

# Add the src directory to Python path
src_dir = Path(__file__).parent / "api" / "src"
sys.path.insert(0, str(src_dir))

# Load environment variables from .env-dev file
try:
    from dotenv import load_dotenv
    env_file = Path(__file__).parent / ".env-dev"
    if env_file.exists():
        load_dotenv(env_file)
        print(f"Loaded environment from {env_file}")
except ImportError:
    print("python-dotenv not installed, using system environment variables")

from sqlalchemy import create_engine, text

def init_database():
    """Initialise the database with required tables."""
    db_dsn = os.getenv('DB_DSN', 'postgresql+psycopg2://daygle_mail_archiver:change_me@localhost:5432/daygle_mail_archiver')

    print(f"Initialising database: {db_dsn}")

    # Create engine
    engine = create_engine(db_dsn)

    with engine.begin() as conn:
        # Create logs table
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS logs (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
                level TEXT NOT NULL,
                source TEXT NOT NULL,
                message TEXT NOT NULL,
                details TEXT
            )
        '''))

        # Create settings table
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        '''))

        # Create users table
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                last_login TIMESTAMP WITH TIME ZONE
            )
        '''))

        # Create alert_triggers table
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS alert_triggers (
                id SERIAL PRIMARY KEY,
                trigger_key TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                alert_type TEXT NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT TRUE
            )
        '''))

        # Create alerts table
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS alerts (
                id SERIAL PRIMARY KEY,
                trigger_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                severity TEXT NOT NULL,
                acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                acknowledged_at TIMESTAMP WITH TIME ZONE,
                FOREIGN KEY (trigger_id) REFERENCES alert_triggers(id)
            )
        '''))

        # Insert default admin user (password: admin)
        conn.execute(text('''
            INSERT INTO users (username, password_hash, role, created_at)
            VALUES ('admin', '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewfLkIwF5zl5ZL2e', 'admin', NOW())
            ON CONFLICT (username) DO NOTHING
        '''))

        # Ensure theme_preference column exists (Postgres supports IF NOT EXISTS)
        conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS theme_preference TEXT NOT NULL DEFAULT 'system'"))

        # Insert some default settings
        default_settings = [
            ('date_format', '%Y-%m-%d'),
            ('time_format', '%H:%M:%S'),
            ('timezone', 'UTC'),
            ('enable_alerts', 'true'),
            ('setup_complete', 'false')
        ]
        for key, value in default_settings:
            conn.execute(text(f"INSERT INTO settings (key, value) VALUES ('{key}', '{value}') ON CONFLICT (key) DO NOTHING"))

        # Insert some default alert triggers
        default_triggers = [
            ('worker_heartbeat', 'Worker Heartbeat', 'Monitor worker process health', 'system', 1),
            ('fetch_error', 'Fetch Error', 'Email fetch failures', 'error', 1),
            ('storage_full', 'Storage Full', 'Disk space running low', 'warning', 1)
        ]
        for key, name, desc, alert_type, enabled in default_triggers:
            conn.execute(text(f"INSERT INTO alert_triggers (trigger_key, name, description, alert_type, enabled) VALUES ('{key}', '{name}', '{desc}', '{alert_type}', {enabled}) ON CONFLICT (trigger_key) DO NOTHING"))

    print("Database initialised successfully!")
    print("Default admin user: admin / admin")

if __name__ == "__main__":
    init_database()