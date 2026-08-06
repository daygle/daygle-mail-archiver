# Configuration and deployment

## First-time setup

1. Copy the example configuration. Change the database `password = change_me` placeholder as well as every `CHANGE_ME_*` security value:

   ```bash
   cp daygle_mail_archiver.conf.example daygle_mail_archiver.conf
   ```

2. Generate the security secrets with:

   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

3. Keep the same `DB_DSN`/PostgreSQL credentials and `IMAP_PASSWORD_KEY` available to both the API and worker. The quarantine key is separate and is only required when quarantine encryption is enabled.
4. Start the stack:

   ```bash
   docker compose up -d --build
   ```

5. Open `http://localhost:8000` and complete the setup wizard. The first administrator receives the initial account and can then configure accounts, roles, settings, and alerts.

The example file is intentionally a template. Never deploy it unchanged and never commit real secrets.

## Configuration sources

The API and worker read environment variables and the optional `daygle_mail_archiver.conf` INI file. Environment variables take precedence. Important values include:

| Setting | Required/when used | Description |
| --- | --- | --- |
| `DB_DSN` or `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | Always | PostgreSQL connection |
| `SESSION_SECRET` | API | Signs session cookies; use a unique high-entropy value |
| `IMAP_PASSWORD_KEY` | API and worker | Fernet key for stored IMAP passwords |
| `CLAMAV_QUARANTINE_KEY` | Worker/API when encryption is enabled | Dedicated Fernet key for quarantined raw messages |
| `ALLOWED_ORIGINS` | API | Comma-separated CORS origins; use an explicit list in production |
| `SESSION_HTTPS_ONLY` | API behind HTTPS | Marks session cookies Secure |
| `PUBLIC_BASE_URL` | OAuth behind a reverse proxy | External URL used to build exact Gmail and Microsoft callback URLs |
| `CLAMAV_HOST`, `CLAMAV_PORT` | API/worker defaults | ClamAV service address |

Database-backed settings such as scan action, scan size, quarantine retention, failure grace period, SMTP, retention, page size, and auto-logout are managed from **Global Settings** after setup.

## Reverse proxy and OAuth

When the API is behind a reverse proxy, configure the public HTTPS origin, for example:

```ini
[security]
public_base_url = https://archive.example.com
session_https_only = true
allowed_origins = https://archive.example.com
```

Register the redirect URI displayed in the Fetch Account edit form with Google or Microsoft. The account ID is part of the callback path. The registered URI must match the generated scheme, host, port, and path exactly.

## Backups and updates

Backups include a PostgreSQL dump and `daygle_mail_archiver.conf`, which means they contain email data, database credentials, and encryption keys. Restrict access and test restores on a non-production system.

```bash
./backup_restore.sh backup
./backup_restore.sh list
./backup_restore.sh restore <backup_file.tar.gz>
```

The update script does not create a database backup automatically. It runs `git add -A` and may create local Git commits while preserving the working state, so ensure private configuration and secrets are ignored before running it. Back up first:

```bash
./backup_restore.sh backup
./update.sh --check
./update.sh
```

Use `./update.sh --apply-db` to apply the idempotent schema to already-running containers without performing a code/image update. Review the output and application health after every update.
