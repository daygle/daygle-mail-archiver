"""Code shared between the API and worker processes.

Modules here must not import from either application's package - each process
wraps them with its own configuration (e.g. the resolved DB_DSN).
"""
