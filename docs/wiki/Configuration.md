# Configuration

This guide covers all configuration options for Daygle Mail Archiver.

## Configuration File

The main configuration file is `daygle_mail_archiver.conf`. Create it from the example:

```bash
cp daygle_mail_archiver.conf.example daygle_mail_archiver.conf
```

## Configuration Priority

Configuration is loaded in the following order (highest priority first):
1. Environment variables
2. `daygle_mail_archiver.conf` file

## Configuration Sections

### Database Settings

```ini
[database]
name = daygle_mail_archiver
user = daygle_mail_archiver
password = change_me
host = db
port = 5432
```

- **name**: Database name
- **user**: Database username
- **password**: Database password (**change this in production!**)
- **host**: Database hostname (use `db` for Docker Compose)
- **port**: Database port (default: 5432)

### Security Settings

```ini
[security]
session_secret = 8f4c2b9e3d7a4f1c9e8b2d3f7c6a1e4b5d8f0c2a7b9d3e6f1a4c7b8d9e2f3a1
imap_password_key = 8t2y0x8qZp8G7QfVYp4p0Q2u7v8Yx1m4l8e0q2c3s0A=
```

- **session_secret**: Secret key for session encryption (**change this in production!**)
- **imap_password_key**: Fernet key for encrypting IMAP passwords (**change this in production!**)

#### Generating Security Keys

Generate a new Fernet key for `imap_password_key`:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Generate a new session secret:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

## Global Settings (Web UI)

After installation, configure global settings through the web interface:

### General Settings

1. Navigate to **Global Settings** in the sidebar
2. Configure:
   - **Items Per Page**: Number of emails per page (10-500)
   - **Date Format**: Display format for dates
   - **Time Format**: 12-hour or 24-hour format
   - **Timezone**: Your preferred timezone

### Retention Policies

Configure automatic email purging:

1. **Enable Automatic Email Purging**: Toggle on to activate
2. **Retention Period**: Set duration (days, months, or years)
3. **Delete from Mail Server**: Enable to also delete from IMAP servers during cleanup

**Example**: Keep emails for 7 years, then automatically delete.

### ClamAV Virus Scanning

Configure virus scanning behavior:

1. **Enable Virus Scanning**: Toggle on to scan incoming emails
2. **ClamAV Host**: Hostname (default: `clamav` for Docker)
3. **ClamAV Port**: Port number (default: 3310)
4. **Action When Virus Detected**:
   - **Quarantine**: Store with virus flag for review
   - **Reject**: Do not store infected emails
   - **Log Only**: Store all emails, log detections

See [ClamAV Virus Scanning](ClamAV-Virus-Scanning.md) for detailed setup.

## Environment Variables

You can override configuration file settings using environment variables:

```bash
# Database
DB_NAME=daygle_mail_archiver
DB_USER=daygle_mail_archiver
DB_PASSWORD=your_password
DB_HOST=db
DB_PORT=5432

# Security
SESSION_SECRET=your_session_secret
IMAP_PASSWORD_KEY=your_fernet_key
```

Edit `docker-compose.yml` to add environment variables to the API and worker services.

## Docker Compose Configuration

The `docker-compose.yml` file defines the services and their relationships. Key sections:

### Port Mapping

```yaml
services:
  api:
    ports:
      - "8000:8000"
```

Change `8000:8000` to use a different port (e.g., `8080:8000`).

### Volume Mounts

```yaml
volumes:
  - ./daygle_mail_archiver.conf:/app/daygle_mail_archiver.conf:ro
```

Configuration file is mounted read-only.

### Resource Limits

You can add resource limits to services:

```yaml
services:
  api:
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 512M
```

## Common Configuration Tasks

### Change API Port

Edit `docker-compose.yml`:

```yaml
services:
  api:
    ports:
      - "8080:8000"  # External:Internal
```

Then restart:

```bash
docker compose down
docker compose up -d
```

### Change Database Password

1. Update `daygle_mail_archiver.conf`
2. Update `docker-compose.yml` environment variables for the `db` service
3. Delete the database volume and restart (data will be lost):

```bash
docker compose down -v
docker compose up -d
```

### Enable External Database

To use an external PostgreSQL database:

1. Update `daygle_mail_archiver.conf` with external database details
2. Remove or comment out the `db` service in `docker-compose.yml`
3. Ensure the external database has the schema from `db/schema.sql` applied

## Next Steps

- [Set up email accounts](Email-Accounts-Setup.md)
- [Configure virus scanning](ClamAV-Virus-Scanning.md)
- [Set up retention policies](#retention-policies)
