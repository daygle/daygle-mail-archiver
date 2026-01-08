#!/usr/bin/env python3
"""
Daygle Mail Archiver - Database Initialization
Initialize the database with required tables for development.
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
    """Initialize the database with required tables."""
    db_dsn = os.getenv('DB_DSN', 'sqlite:///./test.db')

    print(f"Initializing database: {db_dsn}")

    # Create engine
    engine = create_engine(db_dsn)

    with engine.begin() as conn:
        # Create logs table
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL,
                last_login TEXT
            )
        '''))

        # Create alert_triggers table
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS alert_triggers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_key TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                description TEXT,
                alert_type TEXT NOT NULL,
                enabled BOOLEAN NOT NULL DEFAULT 1
            )
        '''))

        # Create alerts table
        conn.execute(text('''
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                severity TEXT NOT NULL,
                acknowledged BOOLEAN NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                acknowledged_at TEXT,
                FOREIGN KEY (trigger_id) REFERENCES alert_triggers(id)
            )
        '''))

        # Insert default admin user (password: admin)
        conn.execute(text('''
            INSERT OR IGNORE INTO users (username, password_hash, role, created_at)
            VALUES ('admin', '$2b$12$LQv3c1yqBWVHxkd0LHAkCOYz6TtxMQJqhN8/LewfLkIwF5zl5ZL2e', 'admin', datetime('now'))
        '''))

        # Insert some default settings
        default_settings = [
            ('date_format', '%Y-%m-%d'),
            ('time_format', '%H:%M:%S'),
            ('timezone', 'UTC'),
            ('enable_alerts', 'true'),
            ('setup_complete', 'false')
        ]
        for key, value in default_settings:
            conn.execute(text(f"INSERT OR IGNORE INTO settings (key, value) VALUES ('{key}', '{value}')"))

        # Insert some default alert triggers
        default_triggers = [
            ('worker_heartbeat', 'Worker Heartbeat', 'Monitor worker process health', 'system', 1),
            ('fetch_error', 'Fetch Error', 'Email fetch failures', 'error', 1),
            ('storage_full', 'Storage Full', 'Disk space running low', 'warning', 1)
        ]
        for key, name, desc, alert_type, enabled in default_triggers:
            conn.execute(text(f"INSERT OR IGNORE INTO alert_triggers (trigger_key, name, description, alert_type, enabled) VALUES ('{key}', '{name}', '{desc}', '{alert_type}', {enabled})"))

    print("Database initialized successfully!")
    print("Default admin user: admin / admin")

if __name__ == "__main__":
    init_database()