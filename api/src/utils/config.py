"""
Configuration loader for Daygle Mail Archiver.

This module handles loading configuration from either .conf files (preferred)
or .env files (for backward compatibility). It uses a centralized approach
to make configuration management more maintainable.

Priority order:
1. .conf file (INI format) - preferred
2. .env file (legacy) - for backward compatibility
3. Environment variables - highest priority override
"""
import os
import configparser
from pathlib import Path
from typing import Optional


class Config:
    """Configuration manager that supports both .conf and .env formats."""
    
    def __init__(self):
        self._config = {}
        self._load_config()
    
    def _load_config(self):
        """Load configuration from .conf file, .env file, or environment variables."""
        # Check multiple possible locations for config files
        # 1. In /app (when running in Docker)
        # 2. In project root (when running locally for development)
        possible_roots = [
            Path('/app'),  # Docker container
            Path(__file__).parent.parent.parent.parent,  # Local development
        ]
        
        conf_file = None
        env_file = None
        
        for root_dir in possible_roots:
            conf_candidate = root_dir / ".conf"
            env_candidate = root_dir / ".env"
            
            if conf_candidate.exists():
                conf_file = conf_candidate
                break
            elif env_candidate.exists():
                env_file = env_candidate
        
        # Try loading from .conf file first (preferred)
        if conf_file and conf_file.exists():
            self._load_from_conf(conf_file)
        # Fall back to .env file for backward compatibility
        elif env_file and env_file.exists():
            self._load_from_env(env_file)
        
        # Environment variables always take precedence
        self._load_from_environment()
    
    def _load_from_conf(self, conf_file: Path):
        """Load configuration from INI-style .conf file."""
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
            
            # Also set PostgreSQL container environment variables
            self._config['POSTGRES_DB'] = self._config.get('DB_NAME')
            self._config['POSTGRES_USER'] = self._config.get('DB_USER')
            self._config['POSTGRES_PASSWORD'] = self._config.get('DB_PASS')
        
        # Security section
        if parser.has_section('security'):
            self._config['SESSION_SECRET'] = parser.get('security', 'session_secret', fallback=None)
            self._config['IMAP_PASSWORD_KEY'] = parser.get('security', 'imap_password_key', fallback=None)
    
    def _load_from_env(self, env_file: Path):
        """Load configuration from .env file for backward compatibility."""
        with open(env_file, 'r') as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if not line or line.startswith('#'):
                    continue
                # Parse KEY=VALUE format
                if '=' in line:
                    key, value = line.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    # Handle variable substitution like ${DB_NAME}
                    # Add max iterations to prevent infinite loops
                    max_iterations = 10
                    iteration = 0
                    while '${' in value and '}' in value and iteration < max_iterations:
                        var_start = value.index('${')
                        var_end = value.index('}', var_start)
                        var_name = value[var_start+2:var_end]
                        var_value = self._config.get(var_name, os.getenv(var_name, ''))
                        value = value[:var_start] + var_value + value[var_end+1:]
                        iteration += 1
                    self._config[key] = value
    
    def _load_from_environment(self):
        """Load configuration from environment variables (highest priority)."""
        env_vars = [
            'DB_NAME', 'DB_USER', 'DB_PASS', 'DB_DSN',
            'POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD',
            'SESSION_SECRET', 'IMAP_PASSWORD_KEY'
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
