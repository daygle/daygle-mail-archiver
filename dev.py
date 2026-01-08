#!/usr/bin/env python3
"""
Daygle Mail Archiver - Development Runner
Run the FastAPI application with auto-reload for development.
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

# Import and run uvicorn
import uvicorn

if __name__ == "__main__":
    print("Starting Daygle Mail Archiver in development mode...")
    uvicorn.run(
        "api.src.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[str(Path(__file__).parent / "api" / "src")]
    )