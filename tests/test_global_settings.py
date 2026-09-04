"""
Unit tests for global-settings server-side validation.

The HTML form constrains most fields, but the server must not trust the client:
a destructive value such as ``retention_value=0`` would make the worker purge
the *entire* archive on its next retention run. These tests exercise the pure
validation function; no database or network required.

Run with:  python -m pytest tests/ -v
"""

import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet

API_DIR = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(API_DIR))
os.environ.setdefault("DB_DSN", "postgresql+psycopg2://test:test@localhost:5432/test")
os.environ.setdefault("IMAP_PASSWORD_KEY", Fernet.generate_key().decode())
os.environ.setdefault("SESSION_SECRET", "test-secret")

from src.routes.global_settings import validate_global_settings  # noqa: E402


def _valid_values(**overrides):
    values = {
        "page_size": 50,
        "retention_value": 1,
        "retention_unit": "years",
        "auto_logout_minutes": 60,
        "clamav_port": 3310,
        "clamav_max_file_size": 10485760,
        "clamav_quarantine_retention_days": 90,
        "clamav_action": "quarantine",
        "smtp_port": 587,
        "clamav_failure_grace_seconds": 300,
        "timezone": "UTC",
    }
    values.update(overrides)
    return values


def test_all_valid_values_pass():
    assert validate_global_settings(_valid_values()) is None


def test_page_size_bounds():
    assert validate_global_settings(_valid_values(page_size=10)) is None
    assert validate_global_settings(_valid_values(page_size=500)) is None
    assert validate_global_settings(_valid_values(page_size=9)) is not None
    assert validate_global_settings(_valid_values(page_size=501)) is not None
    assert validate_global_settings(_valid_values(page_size="abc")) is not None


def test_retention_value_must_be_positive():
    """0/negative would purge the whole archive - must be rejected."""
    assert validate_global_settings(_valid_values(retention_value=1)) is None
    assert validate_global_settings(_valid_values(retention_value=0)) is not None
    assert validate_global_settings(_valid_values(retention_value=-5)) is not None


def test_retention_unit_enum():
    assert validate_global_settings(_valid_values(retention_unit="days")) is None
    assert validate_global_settings(_valid_values(retention_unit="months")) is None
    assert validate_global_settings(_valid_values(retention_unit="decades")) is not None


def test_auto_logout_bounds():
    assert validate_global_settings(_valid_values(auto_logout_minutes=0)) is None  # disabled
    assert validate_global_settings(_valid_values(auto_logout_minutes=1440)) is None
    assert validate_global_settings(_valid_values(auto_logout_minutes=-1)) is not None
    assert validate_global_settings(_valid_values(auto_logout_minutes=1441)) is not None


def test_ports_must_be_in_range():
    assert validate_global_settings(_valid_values(clamav_port=1)) is None
    assert validate_global_settings(_valid_values(clamav_port=65535)) is None
    assert validate_global_settings(_valid_values(clamav_port=0)) is not None
    assert validate_global_settings(_valid_values(clamav_port=70000)) is not None
    assert validate_global_settings(_valid_values(smtp_port=0)) is not None


def test_clamav_max_file_size_minimum():
    assert validate_global_settings(_valid_values(clamav_max_file_size=1024)) is None
    assert validate_global_settings(_valid_values(clamav_max_file_size=100)) is not None


def test_quarantine_retention_must_be_positive():
    assert validate_global_settings(_valid_values(clamav_quarantine_retention_days=1)) is None
    assert validate_global_settings(_valid_values(clamav_quarantine_retention_days=0)) is not None


def test_clamav_action_enum():
    assert validate_global_settings(_valid_values(clamav_action="reject")) is None
    assert validate_global_settings(_valid_values(clamav_action="log_only")) is None
    assert validate_global_settings(_valid_values(clamav_action="delete_everything")) is not None


def test_grace_period_bounds():
    assert validate_global_settings(_valid_values(clamav_failure_grace_seconds=0)) is None
    assert validate_global_settings(_valid_values(clamav_failure_grace_seconds=86400)) is None
    assert validate_global_settings(_valid_values(clamav_failure_grace_seconds=-1)) is not None


def test_timezone_validation():
    assert validate_global_settings(_valid_values(timezone="Australia/Melbourne")) is None
    assert validate_global_settings(_valid_values(timezone="Mars/Olympus_Mons")) is not None


def test_legacy_timezone_does_not_block_other_saves():
    """A pre-existing non-canonical timezone must not block saving other
    settings - only *changed* timezone selections are validated."""
    legacy = "Mars/Olympus_Mons"
    # Unchanged odd timezone -> allowed
    assert validate_global_settings(_valid_values(timezone=legacy), current_timezone=legacy) is None
    # Changing FROM a legacy value to another invalid one -> still rejected
    assert (
        validate_global_settings(_valid_values(timezone="Moon/Sea_of_Storms"), current_timezone=legacy)
        is not None
    )
    # Changing FROM legacy to a valid zone -> allowed
    assert (
        validate_global_settings(_valid_values(timezone="Pacific/Auckland"), current_timezone=legacy)
        is None
    )
