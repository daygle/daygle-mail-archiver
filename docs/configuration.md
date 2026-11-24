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

## Container image updates

Dependabot's `docker` ecosystem only reads Dockerfiles and Kubernetes manifests, so the images pinned in `docker-compose.yml` (`postgres:17` and `clamav/clamav:latest`) are outside its reach. `check_base_images.py` covers them, and `.github/workflows/base-images.yml` runs it every Monday at 06:00 UTC:

```bash
python3 check_base_images.py            # summary table; exit 1 when a bump is due
python3 check_base_images.py --verbose  # also print the newest release found
```

Exit codes: `0` everything tracks the newest release, `1` a tag can be bumped, `2` the check could not run (usually Docker Hub unreachable, or the compose file could not be parsed). A red **Base image updates** run means the same thing as exit `1`; the table is repeated in the run summary.

The check reports two kinds of drift:

- **A tag falling behind its line** - a pinned patch such as `clamav/clamav:1.5.4` while `1.5.5` is published, or a `latest`/floating tag whose digest no longer matches the newest release.
- **A newer major release** - for example `postgres:17` while `18.x` or `19.x` exist.

Bumping a tag:

```bash
# 1. Edit the tag in docker-compose.yml
# 2. Pull the new image and recreate the containers
docker compose pull
docker compose up -d

# 3. Confirm the check passes again
python3 check_base_images.py
```

Per-image guidance:

- **PostgreSQL** - the floating `postgres:17` tag already tracks every 17.x release, so remaining on the same major only needs a pull. Moving to a new major (`18`, `19`, ...) requires a logical dump/restore (`pg_dump`/`pg_restore`) or `pg_upgrade`. Back up first with `./backup_restore.sh backup`, and rehearse the restore on a non-production copy before touching the archive.
- **ClamAV** - prefer a versioned tag such as `clamav/clamav:1.5.5` over `latest`, so a redeploy is reproducible and the running scanner version is known. Confirm scanning still works from **Global Settings** after the change.

If a new major has been reviewed and you are deliberately staying on the current line, record it in `DEFERRED_MAJORS` at the top of `check_base_images.py`, for example `"postgres": 19`. That entry means "majors up to 19 have been considered"; the check reports the finding as `DEFERRED` instead of failing, and only starts failing again when a later major appears.
