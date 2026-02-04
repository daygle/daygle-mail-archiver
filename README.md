# Daygle Mail Archiver

Daygle Mail Archiver is a deterministic email ingestion and archiving system designed for long‑term retention, auditability, and operational reliability. It ingests emails from multiple sources (IMAP, Gmail API, Office 365 Graph API), stores messages in a structured database, and exposes a clean UI for browsing, retention policy management, and administrative control.

This project is built with explicit, maintainable configuration, modular backend logic, and a modernised UI - ensuring predictable behaviour across all environments.

---

## ✨ Features

- **Multi-Source Email Fetching**: IMAP/IMAPS, Gmail API, Office 365 Graph API with delta sync
- **Automatic Email Archiving**: Continuously polls accounts and stores emails
- **Search & Filter**: Full-text search across subjects, senders, and recipients
- **Raw Email Storage**: Complete RFC822 format with compression
- **Email Integrity Verification**: Cryptographic signature verification for archived emails
- **Retention Policies**: Automatic purging based on configurable rules
- **Deletion Tracking**: Dashboard analytics for manual and automated deletions
- **Mail Server Cleanup**: Optional deletion from mail servers during retention cleanup
- **User Management**: Multi-user system with role-based access via granular roles and permissions
- **User Status Tracking**: Real-time online/offline status indicators and session management
- **OAuth2 Integration**: Secure authentication for Gmail and Office 365
- **Worker Status Monitoring**: Real-time health monitoring of fetch workers
- **Dashboard Analytics**: Visual charts and customisable widget layouts with per-widget configuration
- **Widget Customization**: Drag-and-drop layout, visibility toggles, and date range settings per widget
- **Test Connection**: Test IMAP, Gmail, and Office 365 connections from the UI
- **Database Backup & Restore**: Built-in backup functionality
- **Audit Logging**: Complete audit trail of all system actions
- **Virus Scanning**: Integrated ClamAV for scanning incoming emails with configurable actions
- **Advanced Reporting**: Email volume trends, account activity, user analytics, and system health reports
- **Email Alerts**: Configurable SMTP alerts for system events, virus detections, and critical issues
- **Alert Management**: Real-time alert dashboard with acknowledgment and email notifications
- **User Preferences**: Per-user email notification settings, theme, timezone, and date/time formats

---

## 📖 Documentation

**Complete documentation is available in the [Wiki](https://github.com/daygle/daygle-mail-archiver/wiki/)**

### Quick Links

- **[Installation Guide](https://github.com/daygle/daygle-mail-archiver/wiki/Installation-Guide)** - Get started with installation and configuration
- **[Fetch Accounts](https://github.com/daygle/daygle-mail-archiver/wiki/Fetch-Accounts-Setup)** - Set up IMAP, Gmail, Office 365
- **[User Management](https://github.com/daygle/daygle-mail-archiver/wiki/User-Management)** - Manage users and roles
- **[Dashboard](https://github.com/daygle/daygle-mail-archiver/wiki/Dashboard)** - Customise your dashboard
- **[ClamAV Virus Scanning](https://github.com/daygle/daygle-mail-archiver/wiki/ClamAV-Virus-Scanning)** - Configure virus scanning
- **[Advanced Reporting](https://github.com/daygle/daygle-mail-archiver/wiki/Advanced-Reporting)** - Email volume, account activity, and system health reports
- **[Email Alerts & Notifications](https://github.com/daygle/daygle-mail-archiver/wiki/Email-Alerts-&-Notifications)** - Configure SMTP alerts and notification system
- **[Backup and Restore](https://github.com/daygle/daygle-mail-archiver/wiki/Backup-and-Restore)** - Backup and restore procedures
- **[Troubleshooting](https://github.com/daygle/daygle-mail-archiver/wiki/Troubleshooting)** - Common issues and solutions
- **[Security Notes](https://github.com/daygle/daygle-mail-archiver/wiki/Security-Notes)** - Security best practices

---

## 🚀 Quick Start

### Prerequisites

- Docker (version 20.10 or higher)
- Docker Compose
- Minimum 4 GB RAM (6 GB recommended with ClamAV)
- 20 GB disk space (more for email archives)

### Installation

```bash
# Clone repository
cd /opt/
git clone https://github.com/daygle/daygle-mail-archiver.git
cd daygle-mail-archiver

# Configure
cp daygle_mail_archiver.conf.example daygle_mail_archiver.conf
# Edit daygle_mail_archiver.conf and change security values!

# Start system
docker compose up -d --build

# Access web interface
# Navigate to http://localhost:8000
```

**Important**: Change default security values in `daygle_mail_archiver.conf` before production use!

See the [Installation Guide](https://github.com/daygle/daygle-mail-archiver/wiki/Installation-Guide) for detailed instructions.

---

## 🏗️ Architecture

Four main components:

1. **PostgreSQL Database** - Stores emails, accounts, users, settings
2. **FastAPI Web Application** - Web UI and REST API (port 8000)
3. **Background Worker** - Email fetching and retention cleanup
4. **ClamAV** - Virus scanning service

All components run in Docker containers orchestrated by Docker Compose.

---

## 📊 Advanced Reporting & Analytics

Daygle Mail Archiver includes comprehensive reporting capabilities to monitor system performance and email processing:

### Report Types
- **Email Volume Reports**: Daily/weekly/monthly email ingestion trends with virus detection statistics
- **Account Activity Reports**: Sync performance, success rates, and email processing per account
- **System Health Reports**: Database growth, error trends, and worker heartbeat monitoring
- **Storage Utilization Reports**: Email storage usage, compression savings, and largest email tracking
- **Retention Policy Reports**: Effectiveness of retention policies and email age distribution
- **System Performance Reports**: Worker activity, processing rates, and system metrics
- **Security & Access Reports**: Login attempts, security events, and user activity (Administrator only)
- **Data Quality Reports**: Email completeness, scan coverage, duplicates, and error rates

### Key Metrics
- Email processing volumes over time
- Account synchronization status
- Virus detection rates
- System performance indicators
- User activity patterns
- Storage utilization and trends
- Retention policy effectiveness
- Security events and access patterns
- Data completeness and quality metrics

Reports are accessible via the **Reports** menu and support customisable date ranges and export capabilities.

---

## 🚨 Email Alerts & Notifications

Stay informed about critical system events with the built-in alert system:

### Alert Types
- **Security Alerts**: Virus detections, authentication failures, suspicious activity
- **System Alerts**: Service failures, configuration errors, performance issues
- **Operational Alerts**: Account sync failures, retention cleanup status, maintenance notifications

### Email Configuration
- **SMTP Support**: Configure any SMTP server (Gmail, Outlook, custom)
- **TLS Encryption**: Secure email delivery with STARTTLS
- **Recipient Management**: Alerts sent to all administrator users
- **Alert Acknowledgment**: Track and manage alert responses

### Alert Management
- **Real-time Dashboard**: View all alerts with filtering and search
- **Email Notifications**: Instant alerts for critical issues
- **Acknowledgment System**: Mark alerts as reviewed
- **Alert History**: Complete audit trail of system events

Configure SMTP settings in **Global Settings** → **SMTP Email Configuration** to enable email alerts.

---

## 🌍 Internationalization

Daygle Mail Archiver supports multiple languages:

- 🇬🇧 **English (en)** - Default
- 🇪🇸 **Spanish (es)** - Español (93% translated)
- 🇫🇷 **French (fr)** - Français (90% translated)
- 🇩🇪 **German (de)** - Deutsch (92% translated)
- 🇨🇳 **Chinese (zh)** - 中文 (99% translated)

Users can select their preferred language:
- On the login page (language picker in top-right)
- After login: **Settings** → **User Settings** → **Language**

---

## 🔄 Updating

System updates are managed via the command line (the web-based update checker has been removed in favor of the more reliable CLI approach).

```bash
# Check for updates
./update.sh --check

# Update system (interactive)
./update.sh

# Update system without prompts
./update.sh --force

# Update but don’t start containers automatically (useful for inspection)
./update.sh --skip-start
```

What the update script does:

-- Fetches and merges the latest code from the current git branch (attempts to preserve local changes by committing current state)
- Pulls updated Docker images via Docker Compose
- Rebuilds and restarts containers (with fallback to `--no-cache` and build cache pruning on failure)
<!-- Local diff saving removed -->

Important: the update script does NOT automatically create a full database backup. You should create a backup before updating if you need to preserve the database state. Use the provided backup script before running `update.sh`:

```bash
# Create a full system backup (database + config)
./scripts/backup_restore.sh backup
```

See [Updating](https://github.com/daygle/daygle-mail-archiver/wiki/Updating) in the wiki for a recommended update workflow, rollback tips, and common troubleshooting steps.

---

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository on [GitHub](https://github.com/daygle/daygle-mail-archiver)
2. Create a feature branch
3. Make your changes
4. Submit a pull request

---

## 📄 License

This project is licensed under the MIT License. See the LICENSE file for details.

---

## 🆘 Support

- **Documentation**: [Wiki](https://github.com/daygle/daygle-mail-archiver/wiki/)
- **Issues**: [GitHub Issues](https://github.com/daygle/daygle-mail-archiver/issues)
- **Troubleshooting**: [Troubleshooting Guide](https://github.com/daygle/daygle-mail-archiver/wiki/Troubleshooting)

---

## ⚠️ Security

**Before production deployment:**
- Change all default passwords and secrets
- Enable HTTPS via reverse proxy
- Configure firewall rules
- Enable virus scanning
- Configure email alerts for security monitoring
- Set up regular backups

The system provides real-time security alerts for virus detections, authentication failures, and system anomalies. Configure SMTP settings to receive immediate email notifications of security events.

See [Security Notes](https://github.com/daygle/daygle-mail-archiver/wiki/Security-Notes) for complete security guidelines.

---

## 🔐 Quarantine encryption

If you enable **quarantine encryption**, raw quarantined emails will be encrypted at rest using a Fernet key and stored in the `quarantined_emails` table. This prevents accidental exposure of infected email content in database backups or when browsing quarantine entries.

Important notes:

- Do **not** reuse the IMAP password encryption key for quarantine encryption. The system expects a dedicated `CLAMAV_QUARANTINE_KEY` for quarantine data to keep key scopes separate and reduce blast radius.
- Generate a key using:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

- Configure the key (example):

```bash
# Set as an environment variable or in your config
CLAMAV_QUARANTINE_KEY=<paste-base64-fernet-key-here>
```

- Enable encryption in the database:

```sql
UPDATE settings SET value='true' WHERE key='clamav_quarantine_encrypt';
```

- Rotation warning: rotating `CLAMAV_QUARANTINE_KEY` will make previously encrypted quarantined items unreadable unless you re-encrypt them with the new key or maintain the previous key for decryption during migration.

---
