"""
Configuration loader for Daygle Mail Archiver Worker.

This module handles loading configuration from daygle_mail_archiver.conf file (INI format).
Configuration can be overridden by environment variables.

Priority order (highest to lowest):
1. Environment variables - highest priority override
2. daygle_mail_archiver.conf file (INI format) - primary configuration file
"""
import os
import configparser
from pathlib import Path
from typing import Optional


class Config:
    """Configuration manager that loads from daygle_mail_archiver.conf file."""
    
    def __init__(self):
        self._config = {}
        self._load_config()
    
    def _load_config(self):
        """Load configuration from daygle_mail_archiver.conf file or environment variables."""
        # Check multiple possible locations for config files
        # 1. In /app (when running in Docker)
        # 2. In project root (when running locally for development)
        possible_roots = [
            Path('/app'),  # Docker container
            Path(__file__).parent.parent.parent,  # Local development
        ]
        
        conf_file = None
        
        for root_dir in possible_roots:
            conf_candidate = root_dir / "daygle_mail_archiver.conf"
            if conf_candidate.exists():
                conf_file = conf_candidate
                break
        
        # Load from daygle_mail_archiver.conf file
        if conf_file and conf_file.exists():
            self._load_from_conf(conf_file)
        
        # Environment variables always take precedence
        self._load_from_environment()
    
    def _load_from_conf(self, conf_file: Path):
        """Load configuration from INI-style daygle_mail_archiver.conf file."""
        parser = configparser.ConfigParser()
        parser.read(conf_file)
        
        # Database section
        if parser.has_section('database'):
            self._config['DB_NAME'] = parser.get('database', 'name', fallback=None)
            self._config['DB_USER'] = parser.get('database', 'user', fallback=None)
            self._config['DB_PASS'] = parser.get('database', 'password', fallback=None)
            db_host = parser.get('database', 'host', fallback='db')
            db_port = parser.get('database', 'port', fallback='5432')
            
            # Construct DB_DSN if we have all required parts
            if all([self._config.get('DB_USER'), self._config.get('DB_PASS'), 
                   self._config.get('DB_NAME')]):
                self._config['DB_DSN'] = (
                    f"postgresql+psycopg2://{self._config['DB_USER']}:"
                    f"{self._config['DB_PASS']}@{db_host}:{db_port}/"
                    f"{self._config['DB_NAME']}"
                )
        
        # Security section
        if parser.has_section('security'):
            self._config['IMAP_PASSWORD_KEY'] = parser.get('security', 'imap_password_key', fallback=None)
    
    def _load_from_environment(self):
        """Load configuration from environment variables (highest priority)."""
        env_vars = [
            'DB_NAME', 'DB_USER', 'DB_PASS', 'DB_DSN',
            'IMAP_PASSWORD_KEY'
        ]
        for var in env_vars:
            env_value = os.getenv(var)
            if env_value:
                self._config[var] = env_value
    
    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Get a configuration value by key."""
        return self._config.get(key, default)
    
    def require(self, key: str) -> str:
        """Get a required configuration value, raising an error if not found."""
        value = self._config.get(key)
        if not value:
            raise RuntimeError(f"{key} is not set in configuration")
        return value


# Global configuration instance
_config = Config()


def get_config(key: str, default: Optional[str] = None) -> Optional[str]:
    """Get a configuration value by key."""
    return _config.get(key, default)


def require_config(key: str) -> str:
    """Get a required configuration value, raising an error if not found."""
    return _config.require(key)
