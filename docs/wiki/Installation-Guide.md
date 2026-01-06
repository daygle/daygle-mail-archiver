# Installation Guide

This guide will walk you through installing Daygle Mail Archiver on your system.

## System Requirements

### Minimum Requirements

- **CPU**: 2 cores
- **RAM**: 4 GB minimum (6 GB recommended with ClamAV enabled)
- **Disk Space**: 20 GB minimum
  - Database grows based on email volume
  - Plan for additional space based on archiving needs
- **Operating System**: 
  - Linux (Ubuntu 20.04+, Debian 11+, CentOS 8+, etc.)
  - macOS (with Docker Desktop)
  - Windows (with Docker Desktop and WSL2)

### Recommended Requirements

- **CPU**: 4+ cores
- **RAM**: 8 GB or more
- **Disk Space**: 100 GB+ (depends on email volume)
- **SSD Storage**: For better database performance

### Resource Notes

- **ClamAV**: Requires 1-2 GB RAM for virus scanning
- **Database**: PostgreSQL requires ~512 MB minimum
- **Worker**: ~256-512 MB depending on fetch frequency
- **API**: ~256-512 MB for web interface

### Scaling Considerations

For high-volume environments (100,000+ emails):
- **CPU**: 8+ cores recommended
- **RAM**: 16 GB+ recommended
- **Disk**: SSD strongly recommended
- **Database**: Consider dedicated PostgreSQL server

## Prerequisites

Before you begin, ensure you have the following installed:
- **Docker** (version 20.10 or higher)
- **Docker Compose** (either `docker compose` or `docker-compose`)

## Installation Steps

### 1. Clone the Repository

```bash
cd /opt/
git clone https://gitlab.com/daygle/daygle-mail-archiver.git
cd daygle-mail-archiver
```

### 2. Configure the System

Create your configuration file from the example:

```bash
cp daygle_mail_archiver.conf.example daygle_mail_archiver.conf
```

Edit `daygle_mail_archiver.conf` with your settings. See the [Configuration](Configuration.md) guide for detailed information.

**Important:** You must change the default security values in production!

### 3. Build and Start the System

```bash
docker compose up -d --build
```

This will:
- Start PostgreSQL container (`daygle-mail-archiver-database`)
- Apply database schema automatically
- Start ClamAV container (`daygle-mail-archiver-clamav`)
- Start the API container on port 8000 (`daygle-mail-archiver-api`)
- Start the worker container (`daygle-mail-archiver-worker`)

**Note:** The ClamAV container may take 5-10 minutes on first startup to download virus definitions.

### 4. Verify Containers Are Healthy

Check container status:

```bash
docker compose ps
```

All services should show as "healthy" or "running". View logs if needed:

```bash
docker compose logs api
docker compose logs worker
docker compose logs db
docker compose logs clamav
```

### 5. Complete Initial Setup

1. Open your browser and navigate to: `http://localhost:8000`
2. Follow the setup wizard to create your administrator account
3. Log in with your new credentials

## Post-Installation

After installation, you may want to:
- [Set up email accounts](Email-Accounts-Setup.md) to start archiving
- [Configure retention policies](Configuration.md#retention-policies)
- [Set up additional users](User-Management.md)
- [Configure ClamAV virus scanning](ClamAV-Virus-Scanning.md)

## Updating the System

To update Daygle Mail Archiver to the latest version:

### Check for Updates

From the dashboard:
1. Click the notification banner if updates are available
2. Or check manually in the system

From the command line:

```bash
./update.sh --check
```

### Apply Updates

```bash
# Interactive update with confirmation
./update.sh

# Non-interactive update
./update.sh --yes

# Update without restarting containers
./update.sh --no-restart
```

The update script will:
- Create an automatic backup
- Pull the latest code
- Update Docker images
- Restart containers

## Architecture

The Daygle Mail Archiver consists of four main components:

1. **PostgreSQL Database** (`db`): Stores emails, accounts, users, and settings
   - Emails stored as compressed RFC822 format
   - Full-text search indexing
   - Automatic schema initialization

2. **FastAPI Web Application** (`api`): Web UI and REST API
   - Port 8000
   - Session-based authentication
   - OAuth2 integration

3. **Background Worker** (`worker`): Email fetching service
   - Polls enabled accounts
   - Handles retention cleanup
   - Virus scanning integration

4. **ClamAV** (`clamav`): Virus scanning service
   - Scans incoming emails
   - Automatic definition updates
   - Configurable actions

## Next Steps

- [Configure the system](Configuration.md)
- [Set up email accounts](Email-Accounts-Setup.md)
- [Manage users](User-Management.md)
