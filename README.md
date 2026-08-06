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
- **Role-Based Access Control**: Multi-user access management with protected built-in roles, custom roles, and granular permissions
- **Modern Role Management**: Responsive role cards, permission grouping, role search, and category-level permission selection
- **Permission Guardrails**: Privileged permissions cannot be granted or assigned by users who do not already hold them
- **User Status Tracking**: Real-time online/offline status indicators and session management
- **OAuth2 Integration**: Secure authentication for Gmail and Office 365
- **Worker Status Monitoring**: Real-time health monitoring of fetch workers
- **Dashboard Analytics**: Visual charts and customisable widget layouts with per-widget configuration
- **Widget Customization**: Drag-and-drop layout, visibility toggles, and date range settings per widget
- **Test Connection**: Test IMAP, Gmail, and Office 365 connections from the UI
- **Database Backup & Restore**: Command-line backup and restore tooling
- **Audit Logging**: Complete audit trail of all system actions
- **Virus Scanning**: Integrated ClamAV for scanning incoming emails with configurable actions
- **Advanced Reporting**: Email volume trends, account activity, user analytics, and system health reports
- **Email Alerts**: Configurable SMTP alerts for system events, virus detections, and critical issues
- **Alert Management**: Configure trigger enablement and severity separately from in-app alerts and email delivery
- **Audit Logs**: Searchable, filterable logs with level, source, date, and pagination controls
- **User Preferences**: Per-user email notification settings, theme, timezone, and date/time formats

---

## 📖 Documentation

**Complete documentation is available in the [Wiki](https://github.com/daygle/daygle-mail-archiver/wiki/). Maintained local operational guides are also available in [`docs/`](docs/).**

### Quick Links

- **[Installation Guide](https://github.com/daygle/daygle-mail-archiver/wiki/Installation-Guide)** - Get started with installation and configuration
- **[Fetch Accounts](https://github.com/daygle/daygle-mail-archiver/wiki/Fetch-Accounts-Setup)** - Set up IMAP, Gmail, Office 365
- **[User Management](https://github.com/daygle/daygle-mail-archiver/wiki/User-Management)** - Manage users and role assignments
- **[Roles & Permissions](docs/roles-and-permissions.md)** - Design custom roles and review access safely
- **[Quarantine & Integrity](docs/quarantine-and-integrity.md)** - Understand ClamAV scanning, quarantine, restore, and verification
- **[Dashboard](https://github.com/daygle/daygle-mail-archiver/wiki/Dashboard)** - Customise your dashboard
- **[ClamAV Virus Scanning](https://github.com/daygle/daygle-mail-archiver/wiki/ClamAV-Virus-Scanning)** - Configure virus scanning
- **[Advanced Reporting](https://github.com/daygle/daygle-mail-archiver/wiki/Advanced-Reporting)** - Email volume, account activity, and system health reports
- **[Email Alerts & Notifications](https://github.com/daygle/daygle-mail-archiver/wiki/Email-Alerts-&-Notifications)** - Configure SMTP alerts and notification system
- **[Backup and Restore](https://github.com/daygle/daygle-mail-archiver/wiki/Backup-and-Restore)** - Backup and restore procedures
- **[Troubleshooting](https://github.com/daygle/daygle-mail-archiver/wiki/Troubleshooting)** - Common issues and solutions
- **[Security Notes](https://github.com/daygle/daygle-mail-archiver/wiki/Security-Notes)** - Security best practices

Local guides:
- [`docs/configuration.md`](docs/configuration.md) - Configuration, reverse proxies, backups, and updates
- [`docs/roles-and-permissions.md`](docs/roles-and-permissions.md) - RBAC and privilege guardrails
- [`docs/quarantine-and-integrity.md`](docs/quarantine-and-integrity.md) - ClamAV, quarantine, restore, encryption, and integrity checks

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

**Important**: Change default security values in `daygle_mail_archiver.conf` before production use. The example file contains placeholder values and must not be copied unchanged into a public deployment.

For a reverse-proxy deployment, set `public_base_url` to the externally reachable HTTPS origin so Gmail and Microsoft OAuth redirect URIs are generated correctly. Set `session_https_only = true` when the application is served over HTTPS, and replace the wildcard `allowed_origins` value with an explicit comma-separated origin list where appropriate.

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

## 👥 Roles, permissions, and audit logs

Access to administration and data-management features is controlled by roles and granular permissions. The **Role Management** page is available to users with `manage_roles` and provides:

- Built-in protected roles and editable custom roles
- Permission counts and assigned-user counts for each role
- Permission grouping by area, permission search, and select-all controls when creating a role
- Safe protection against privilege escalation: a role manager cannot grant privileged permissions they do not already hold
- Search and filtering across role names, descriptions, and permissions

Users inherit the permissions from all roles assigned to them. The application checks permissions server-side; hiding a navigation item is not used as an authorization boundary. Built-in role assignments and role edits should be reviewed carefully because they affect every user assigned to that role.

The **Logs** page requires `view_logs` and provides searchable, paginated audit information. Filters support log level, source, message text, and date range. Log writes are best-effort so a logging database outage does not mask the original operation.

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
- **Trigger controls**: Enable or disable individual event triggers
- **Severity controls**: Configure `error`, `warning`, `info`, or `success` severity per trigger
- **In-app alerts**: View and acknowledge alerts from the Alerts page
- **Email delivery**: Separate from alert creation; eligible enabled users with the required permission, an email address, and notifications enabled can receive email alerts
- **Trigger-aware callers**: Provider, ClamAV, quarantine, and other alert-producing workflows use the configured trigger settings

Configure SMTP settings in **Global Settings** → **SMTP Email Configuration**, then enable user email notifications where required. A disabled trigger suppresses creation of the corresponding in-app alert; changing severity does not itself enable email delivery.

---

## 🛡️ ClamAV scanning, quarantine, and integrity

Incoming messages are scanned by ClamAV when virus scanning is enabled. Configure the scanner in **Global Settings → Virus Scanning (ClamAV)**:

- `quarantine`: retain detected messages in the quarantine table
- `reject`: do not archive detected messages
- `log_only`: record the detection without quarantine or rejection
- Configure the maximum scan size and the ClamAV failure grace period to avoid reacting to short signature-reload outages

Quarantine records preserve scan metadata, the original source/folder/UID, and the email signature where available. Quarantine records are deduplicated on `(source, folder, UID)` for non-null UIDs, so a fetch-state reset cannot create multiple records for the same provider message. Restoring a message preserves its scan metadata and does not silently overwrite an existing archive row.

Archived messages use a SHA-256 signature over the raw RFC822 bytes. The Emails and Quarantine views expose integrity states such as valid, modified, missing signature, unavailable raw data, or unknown. Integrity verification detects changes; it is not encryption. Optional quarantine encryption uses a dedicated `CLAMAV_QUARANTINE_KEY`.

---

## 🌍 Internationalization

Daygle Mail Archiver supports multiple languages:

- 🇬🇧 **English (en)** - Default
- 🇪🇸 **Spanish (es)** - Español
- 🇫🇷 **French (fr)** - Français
- 🇩🇪 **German (de)** - Deutsch
- 🇨🇳 **Chinese (zh)** - 中文

Users can select their preferred language:
- On the login page (language picker in top-right)
- After login: **Settings** → **User Settings** → **Language**

---

## 🔄 Updating

System updates are managed via the command line (the web-based update checker has been removed in favor of the more reliable CLI approach).

```bash
# Check for updates (short form: -c)
./update.sh --check

# Update system (interactive)
./update.sh

# Update system without prompts (short form: -f)
./update.sh --force

# Update but don’t start containers automatically (useful for inspection)
./update.sh --skip-start

# Apply database schema updates only (containers must already be running)
./update.sh --apply-db

# Show all available options
./update.sh --help
```

What the update script does:

- Fetches and merges the latest code from the current git branch (it may create local update commits while preserving the current working state)
- Pulls updated Docker images via Docker Compose
- Rebuilds and restarts containers (with fallback to `--no-cache` and build-cache pruning on failure)
- Applies the idempotent database schema after the containers start

The update script does not automatically create a full database backup and can modify the local Git checkout. Review the script and create a backup before production updates.

Important: the update script does NOT automatically create a full database backup. You should create a backup before updating if you need to preserve the database state. Use the provided backup script before running `update.sh`:

```bash
# Create a full system backup (database + config)
./backup_restore.sh backup
```

See [Updating](https://github.com/daygle/daygle-mail-archiver/wiki/Updating) in the wiki for a recommended update workflow, rollback tips, and common troubleshooting steps.

---

## 🔑 OAuth2 Setup (Gmail & Office 365)

Fetch accounts using the Gmail API or Office 365 Graph API require an OAuth2 app to be configured before authorisation can complete.

### Office 365 / Azure AD

1. Sign in to the [Azure portal](https://portal.azure.com/) and open **Azure Active Directory** → **App registrations** → **New registration**.
2. Give the app a name, select the supported account types, and leave the Redirect URI blank for now.
3. After creating the app, note the **Application (client) ID** – this is your **OAuth Client ID**.
4. Go to **Certificates & secrets** → **New client secret**, create a secret, and copy the **Value** – this is your **OAuth Client Secret**.
5. Go to **API permissions** → **Add a permission** → **Microsoft Graph** → **Delegated permissions** → add `Mail.Read` and `offline_access`.  Click **Grant admin consent** if required.
6. In Daygle Mail Archiver: go to **Fetch Accounts**, create a new **Office 365 API** account, enter the Client ID and Secret, and save.
7. Open the account for editing.  The **Redirect URI** (e.g. `http://your-host:8000/oauth/o365/callback/<id>`) is shown in the OAuth Configuration section.
8. Back in Azure: **Authentication** → **Add a platform** → **Web** → paste the Redirect URI, then save.
9. Return to the edit form and click **Authorise** to complete the OAuth flow.

### Gmail

1. Open the [Google Cloud Console](https://console.cloud.google.com/), create a project, and enable the **Gmail API**.
2. Go to **APIs & Services** → **Credentials** → **Create credentials** → **OAuth client ID**.  Choose **Web application**.
3. Note the **Client ID** and **Client Secret**.
4. In Daygle Mail Archiver: go to **Fetch Accounts**, create a new **Gmail API** account, enter the Client ID and Secret, and save.
5. Open the account for editing.  The **Redirect URI** (e.g. `http://your-host:8000/oauth/gmail/callback/<id>`) is shown in the OAuth Configuration section.
6. Back in Google Cloud Console: add the Redirect URI to **Authorised redirect URIs**, then save.
7. Return to the edit form and click **Authorise** to complete the OAuth flow.

> **Note:** The `<id>` in the redirect URI is your fetch account's numeric ID, visible in the edit form.

---



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

## 🧰 Configuration reference

The application accepts configuration from environment variables and from the optional `daygle_mail_archiver.conf` INI file. Environment variables take precedence. Keep the API and worker values consistent for shared database and encryption settings.

| Setting | Purpose |
| --- | --- |
| `DB_DSN` or `POSTGRES_*` | PostgreSQL connection settings |
| `SESSION_SECRET` | Signs API session cookies; use a unique high-entropy value |
| `IMAP_PASSWORD_KEY` | Fernet key used to encrypt stored IMAP passwords |
| `CLAMAV_QUARANTINE_KEY` | Dedicated Fernet key for optional quarantined-message encryption |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins; use explicit origins in production |
| `SESSION_HTTPS_ONLY` | Marks the session cookie Secure when the service is behind HTTPS |
| `PUBLIC_BASE_URL` | External application origin used to build OAuth callback URLs behind a proxy |
| `CLAMAV_HOST` / `CLAMAV_PORT` | API/worker connection defaults for ClamAV |

Global ClamAV settings such as scan action, maximum file size, quarantine retention, and failure grace period are stored in the database and managed from the Global Settings page. Do not put secrets in source control, and treat backups as sensitive because they contain database data and encryption material.

---
