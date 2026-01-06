# Daygle Mail Archiver

Daygle Mail Archiver is a deterministic email ingestion and archiving system designed for long‑term retention, auditability, and operational reliability. It ingests emails from multiple sources (IMAP, Gmail API, Office 365 Graph API), stores messages in a structured database, and exposes a clean UI for browsing, retention policy management, and administrative control.

This project is built with explicit, maintainable configuration, modular backend logic, and a modernized UI — ensuring predictable behaviour across all environments.

---

## ✨ Features

- **Multi-Source Email Fetching**: 
  - IMAP/IMAPS with SSL/STARTTLS support
  - Gmail API with OAuth2 authentication
  - Office 365 Graph API with OAuth2 authentication
  - Delta sync for efficient incremental fetching
- **Automatic Email Archiving**: Continuously polls accounts and stores emails
- **Search & Filter**: Full-text search across subjects, senders, and recipients
- **Raw Email Storage**: Stores complete RFC822 format with compression
- **Retention Policies**: Automatic purging of old emails based on configurable rules
- **Deletion Tracking**: Dashboard analytics showing manual and automated deletion statistics
- **Mail Server Cleanup**: Optional deletion from mail servers during retention cleanup
- **User Management**: Multi-user system with role-based access
- **OAuth2 Integration**: Secure authentication for Gmail and Office 365
- **Worker Status Monitoring**: Real-time health monitoring of fetch workers
- **Dashboard Analytics**: Visual charts showing email statistics and trends
- **Customizable Dashboard**: Drag-and-drop widget layout with user preferences
- **Test Connection**: Test IMAP, Gmail, and Office 365 connections directly from the UI
- **Database Backup & Restore**: Built-in database backup and restore functionality
- **Help Documentation**: Built-in help page with comprehensive usage instructions
- **Donation Support**: Integrated PayPal donation page to support development
- **Audit Logging**: Complete audit trail of all system actions
- **Virus Scanning**: Integrated ClamAV for scanning incoming emails with configurable actions

---

## 🏗️ Architecture

The Daygle Mail Archiver consists of four main components:

1. **PostgreSQL Database** (`db`): Stores all emails, accounts, users, settings, and logs
   - Emails stored as compressed RFC822 format in BYTEA columns
   - Full-text search indexing for fast queries
   - Automatic schema initialization on first run

2. **FastAPI Web Application** (`api`): Web UI and REST API
   - FastAPI + Jinja2 templates for the web interface
   - Session-based authentication with bcrypt password hashing
   - OAuth2 integration for Gmail and Office 365
   - Serves on port 8000

3. **Background Worker** (`worker`): Email fetching service
   - Continuously polls enabled fetch accounts
   - Supports IMAP, Gmail API, and Office 365 Graph API
   - Handles retention policy cleanup
   - Scans emails for viruses using ClamAV
   - Updates heartbeat and health status

4. **ClamAV** (`clamav`): Virus scanning service
   - Scans incoming emails for malware and viruses
   - Automatic virus definition updates
   - Configurable actions for infected emails

All components run in Docker containers orchestrated by Docker Compose.

---

# Setup & Installation

Follow these steps to get a fully running Daygle Mail Archiver instance.

---

## 🚀 Getting Started

### **Prerequisites**
- Docker
- Docker Compose (either `docker compose` or `docker-compose`, depending on your system)

### **Clone the Repository**
```bash
cd /opt/
git clone https://gitlab.com/daygle/daygle-mail-archiver.git
cd daygle-mail-archiver
```

---

## ⚙️ Configuration

Create your configuration file from the example:

```bash
cp daygle_mail_archiver.conf.example daygle_mail_archiver.conf
```

Edit `daygle_mail_archiver.conf` with your settings:

```ini
[database]
name = daygle_mail_archiver
user = daygle_mail_archiver
password = change_me
host = db
port = 5432

[security]
session_secret = 8f4c2b9e3d7a4f1c9e8b2d3f7c6a1e4b5d8f0c2a7b9d3e6f1a4c7b8d9e2f3a1
imap_password_key = 8t2y0x8qZp8G7QfVYp4p0Q2u7v8Yx1m4l8e0q2c3s0A=
```

### Configuration Priority

The system loads configuration in the following priority order:
1. Environment variables (highest priority)
2. `daygle_mail_archiver.conf` file

### Important Security Notes

**Important:** Change the default values for `password`, `session_secret`, and `imap_password_key` in production!

To generate a new Fernet key for `imap_password_key`:
```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

To generate a new session secret (`session_secret` in daygle_mail_archiver.conf):
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

---

## 3. Build and start the system

```
docker compose up -d --build
```

This will:

- Start PostgreSQL container (`daygle-mail-archiver-database`)
- Apply database schema from `db/schema.sql` automatically
- Start ClamAV container (`daygle-mail-archiver-clamav`)
- Start the API container on port 8000 (`daygle-mail-archiver-api`)
- Start the worker container (`daygle-mail-archiver-worker`)

**Note:** The ClamAV container may take 5-10 minutes on first startup to download virus definitions. The worker will wait for ClamAV to be healthy before starting.

---

## 4. Verify containers are healthy

```
docker compose ps
```

Expected:

- `daygle-mail-archiver-database` → healthy  
- `daygle-mail-archiver-clamav` → healthy (may take 5-10 minutes on first start)
- `daygle-mail-archiver-api` → running  
- `daygle-mail-archiver-worker` → running  

---

## 5. Complete initial setup

On first access, you'll be prompted to create an administrator account.

Open:

```
http://localhost:8000
```

You will be automatically redirected to the **Setup Wizard** where you can:

1. Choose your administrator username
2. Set your email address (optional)
3. Create a secure password

After completing setup, you'll be redirected to the login page where you can sign in with your new administrator account.  

---

## 6. Updating the System

Daygle Mail Archiver includes an automatic update script similar to mailcow's `update.sh`. The script handles:
- Checking for updates from the git repository
- Pulling the latest code and Docker images
- Restarting containers with minimal downtime

### Check for Updates

```bash
cd /opt/daygle-mail-archiver
./update.sh --check
```

This will show you if updates are available without applying them.

### Update the System

```bash
./update.sh
```

The interactive update process will:
1. Check for available updates
2. Ask for confirmation
3. Save any local changes to `update_diffs/` directory
4. Pull the latest code from git
5. Pull updated Docker images
6. Restart all containers
7. Clean up old Docker resources

### Update Options

```bash
# Update without confirmation prompts
./update.sh --force

# Update but don't restart containers (for manual inspection)
./update.sh --skip-start
```

**Important Notes:**
- Create a manual backup before updating: `./scripts/backup_restore.sh backup`
- Your `daygle_mail_archiver.conf` file is preserved during updates
- Local customizations are saved to `update_diffs/` for your reference

---

## 7. Quick Reference - Common Commands

### System Updates

Daygle Mail Archiver includes an automatic update script that pulls the latest code and Docker images:

```bash
# Check if updates are available
./update.sh --check

# Update the system (interactive, with confirmation)
./update.sh

# Update without confirmation prompts
./update.sh --force

# Update but don't restart containers (for manual inspection)
./update.sh --skip-start
```

**Important:** Create a manual backup before updating: `./scripts/backup_restore.sh backup`. Your configuration file and local customizations are preserved during updates.

### Backup and Restore
```bash
# Create a complete backup (database + configuration file)
./scripts/backup_restore.sh backup

# List available backups
./scripts/backup_restore.sh list

# Restore from backup
./scripts/backup_restore.sh restore daygle_mail_archiver_backup_20240105_120000.tar.gz
```

### Container Management
```bash
# View container status
docker compose ps

# View logs
docker compose logs -f        # All services
docker compose logs -f api    # API only
docker compose logs -f worker # Worker only

# Restart services
docker compose restart

# Stop and remove all containers
docker compose down

# Full reset (removes data)
docker compose down --volumes
```

---

# Setting Up Email Fetch Accounts

The system supports three types of email fetch accounts:

## 1. IMAP Accounts

Traditional IMAP email fetching.

1. Navigate to **Settings → Fetch Accounts**
2. Click **New Fetch Account**
3. Select **Account Type: IMAP**
4. Fill in the details:
   - **Account Name**: A friendly name for the account
   - **Host**: IMAP server hostname (e.g., `imap.gmail.com`)
   - **Port**: Usually `993` for SSL or `143` for STARTTLS
   - **Username**: Your email address
   - **Password**: Account password or app-specific password
   - **Use SSL**: Enable for secure connections
   - **Poll Interval**: How often to check for new emails (seconds)
5. Click **Test Connection** to verify settings
6. Click **Create Account**

## 2. Gmail API

Fetch emails directly from Gmail using OAuth2 (more reliable than IMAP).

### Prerequisites

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select existing)
3. Enable the **Gmail API**:
   - Navigate to **APIs & Services → Library**
   - Search for "Gmail API"
   - Click **Enable**
4. Create OAuth 2.0 credentials:
   - Go to **APIs & Services → Credentials**
   - Click **Create Credentials → OAuth client ID**
   - Select **Web application**
   - Add authorized redirect URI: `http://your-domain/oauth/gmail/callback/{account_id}`
     - Replace `your-domain` with your actual domain
     - The `{account_id}` will be replaced automatically (use exactly as shown)
   - Click **Create**
   - Copy the **Client ID** and **Client Secret**

### Setup in Mail Archiver

1. Navigate to **Settings → Fetch Accounts**
2. Click **New Fetch Account**
3. Select **Account Type: Gmail API**
4. Fill in the details:
   - **Account Name**: A friendly name (e.g., "My Gmail Account")
   - **OAuth Client ID**: Paste from Google Cloud Console
   - **OAuth Client Secret**: Paste from Google Cloud Console
   - **Poll Interval**: How often to check for new emails
5. Click **Create Account**
6. After creation, edit the account and click **Authorize Gmail**
7. Sign in with your Google account and grant permissions
8. You'll be redirected back - authorization complete!

The worker will now fetch emails automatically using the Gmail API.

## 3. Office 365 / Outlook.com

Fetch emails from Office 365 or Outlook.com using Microsoft Graph API.

### Prerequisites

1. Go to [Azure Portal](https://portal.azure.com/)
2. Navigate to **Azure Active Directory → App registrations**
3. Click **New registration**
4. Fill in the details:
   - **Name**: "Mail Archiver" (or any name)
   - **Supported account types**: Choose based on your needs:
     - Single tenant (organization only)
     - Multi-tenant (any organization)
     - Multi-tenant + personal accounts (recommended for Outlook.com)
   - **Redirect URI**: Select **Web** and enter:
     - `http://your-domain/oauth/o365/callback/{account_id}`
     - Replace `your-domain` with your actual domain
5. Click **Register**
6. Copy the **Application (client) ID**
7. Create a client secret:
   - Go to **Certificates & secrets**
   - Click **New client secret**
   - Add description and set expiry
   - Click **Add**
   - **Copy the secret value immediately** (it won't be shown again!)
8. Configure API permissions:
   - Go to **API permissions**
   - Click **Add a permission**
   - Select **Microsoft Graph**
   - Select **Delegated permissions**
   - Add: `Mail.Read` and `offline_access`
   - Click **Add permissions**
   - *Optional*: Click **Grant admin consent** if you're an admin

### Setup in Mail Archiver

1. Navigate to **Settings → Fetch Accounts**
2. Click **New Fetch Account**
3. Select **Account Type: Office 365 API**
4. Fill in the details:
   - **Account Name**: A friendly name (e.g., "Work Office 365")
   - **OAuth Client ID**: Application (client) ID from Azure
   - **OAuth Client Secret**: Client secret value from Azure
   - **Poll Interval**: How often to check for new emails
5. Click **Create Account**
6. After creation, edit the account and click **Authorize Office 365**
7. Sign in with your Microsoft account and grant permissions
8. You'll be redirected back - authorization complete!

The worker will now fetch emails automatically using Microsoft Graph API.

---

# Email Deletion Behavior

When "Delete Email After Processed" is enabled, emails are removed from the mail server after being archived. The behavior differs by account type:

| Account Type | Delete After Processing | Expunge Deleted (IMAP only) | Result |
|--------------|------------------------|------------------------------|--------|
| IMAP | ❌ Off | N/A | Nothing happens - emails remain on server |
| IMAP | ✅ On | ❌ Off | Marks as deleted (recoverable via mail client) |
| IMAP | ✅ On | ✅ On | Marks as deleted AND permanently expunges (not recoverable) |
| Gmail | ❌ Off | N/A | Nothing happens - emails remain in inbox |
| Gmail | ✅ On | N/A | Moves to Trash (30-day recovery period) |
| O365 | ❌ Off | N/A | Nothing happens - emails remain in inbox |
| O365 | ✅ On | N/A | Moves to Deleted Items (recoverable) |

**Notes:**
- **IMAP**: Messages marked with `\Deleted` flag appear deleted in most mail clients but remain on the server until expunged
- **Gmail**: Messages moved to Trash are automatically deleted after 30 days
- **Office 365**: Messages in Deleted Items can be recovered until manually emptied or auto-deleted
- **Warning**: The "Expunge Deleted" option for IMAP permanently removes messages and cannot be undone!

---

# Worker Status Monitoring

Monitor the health and activity of email fetch workers:

1. Navigate to **Settings → Worker Status** (from the sidebar menu)
2. View real-time status for each account:
   - **Health Status**: Healthy, Stale, Error, Pending, or Disabled
   - **Last Heartbeat**: When the worker last checked the account
   - **Last Success**: When emails were last successfully fetched
   - **Last Error**: Any error messages from failed attempts
3. Click the **Test** button on any fetch account to verify connectivity
4. Use this page to troubleshoot connection issues or OAuth problems

---

# Browsing Emails

Click **Emails** in the main navigation menu (sidebar or top menu).

You can:

- **Search**: Full-text search across subjects, senders, and recipients
- **Filter**: Filter by source account or folder
- **View**: Click on any email to view the full message details
- **Download**: Download raw `.eml` files for archival or import into other clients
- **Delete**: Remove individual emails or perform bulk deletions

---

# Retention Policy & Deletion Management

## Configuring Retention

Navigate to **Settings → Global Settings** to configure email retention:

1. **Enable Email Purging**: Check this to activate automatic cleanup
2. **Retention Period**: Set how long to keep emails (e.g., "1 years", "90 days")
3. **Delete from Mail Server**: Enable to also remove emails from original mail servers during retention cleanup
   - When enabled, IMAP accounts will have emails permanently expunged
   - Gmail/Office 365 accounts currently only delete from database (mail server deletion pending OAuth implementation)

## Deletion Behavior

The system tracks all deletions for dashboard analytics:

- **Manual Deletions**: When users delete emails through the UI
  - "Database Only": Removes from archive, keeps on mail server
  - "Database and Mail Server": Removes from both locations
- **Retention Cleanup**: Automated deletions based on retention policy
  - Runs automatically during worker cycles
  - Can optionally delete from mail servers
  - Statistics tracked separately from manual deletions

## Dashboard Statistics

The **Dashboard** displays deletion analytics:
- Manual vs retention deletions over last 30 days
- Count of emails deleted from mail servers
- Visual charts showing deletion patterns

---

# Virus Scanning with ClamAV

The system includes integrated ClamAV virus scanning to protect against malware in incoming emails.

## Configuring Virus Scanning

Navigate to **Settings → Global Settings** to configure ClamAV:

1. **Enable Virus Scanning**: Check this to activate virus scanning for all incoming emails
2. **ClamAV Host**: Hostname of the ClamAV daemon (default: `clamav` for Docker)
3. **ClamAV Port**: Port number for ClamAV daemon (default: `3310`)
4. **Action When Virus Detected**: Choose how to handle infected emails:
   - **Quarantine** (default): Store the email but mark it as infected for review
   - **Reject**: Do not store infected emails in the database
   - **Log Only**: Store all emails but log virus detections for monitoring

## How Virus Scanning Works

1. **Pre-Storage Scanning**: Emails are scanned **before** being saved to the database
2. **Real-Time Detection**: ClamAV scans each incoming email as it's fetched from mail servers
3. **Automatic Updates**: ClamAV automatically updates virus definitions
4. **Visual Indicators**: Email list and detail views show virus scan status with color-coded badges

## Viewing Virus Scan Results

### In Email List

Each email displays a virus scan indicator:
- ✓ Green badge: Clean (no virus detected)
- ⚠️ Red badge: Infected (virus detected - hover for virus name)
- − Gray badge: Not scanned (scanning was disabled)

### In Email Details

When viewing an email, the virus scan section shows:
- Scan status (Clean or Infected)
- Virus name (if detected)
- Scan timestamp

## Handling Infected Emails

### Quarantine Mode (Default)

Infected emails are:
- Stored in the database with a warning flag
- Clearly marked in the UI with red warning badges
- Logged in the system logs with virus name and details
- Can be reviewed by administrators before deletion

### Reject Mode

Infected emails are:
- **Not stored** in the database
- Logged in system logs for audit trail
- The worker continues processing other emails normally

### Log Only Mode

All emails are:
- Stored regardless of virus detection
- Virus detections logged for monitoring
- Useful for testing or when using external virus protection

## ClamAV Container Management

The ClamAV container is automatically managed by Docker Compose:

```bash
# View ClamAV logs
docker compose logs -f clamav

# Restart ClamAV service
docker compose restart clamav

# Update virus definitions (happens automatically)
docker compose exec clamav freshclam
```

**Note:** ClamAV requires approximately 2-3 GB of disk space for virus definitions. The container may take 5-10 minutes to start on first run while downloading definitions.

## Troubleshooting Virus Scanning

### ClamAV Not Starting

Check ClamAV logs for errors:
```bash
docker compose logs clamav
```

Common issues:
- Insufficient disk space for virus definitions
- Network issues preventing definition downloads
- Wait 5-10 minutes for initial setup to complete

### Emails Not Being Scanned

1. Verify ClamAV is running and healthy: `docker compose ps`
2. Check that virus scanning is enabled in Global Settings
3. Review worker logs for connection errors: `docker compose logs -f worker`
4. If ClamAV is unavailable, emails are allowed through and logged

### Disable Virus Scanning

To disable virus scanning:
1. Navigate to **Settings → Global Settings**
2. Uncheck **Enable Virus Scanning**
3. Click **Save Settings**

Alternatively, stop the ClamAV container (not recommended):
```bash
docker compose stop clamav
```

---

# Database Backup & Restore

Protect your email archive with the built-in command-line backup and restore script that includes both the database AND configuration file (encryption keys).

## Command-Line Backup & Restore

The `scripts/backup_restore.sh` script provides a complete backup solution that includes both the database AND the `daygle_mail_archiver.conf` file with encryption keys in a single process.

### Creating a Backup

```bash
cd /opt/daygle-mail-archiver
./scripts/backup_restore.sh backup
```

This creates a timestamped backup file in `./backups/` directory (e.g., `daygle_mail_archiver_backup_20240105_120000.tar.gz`) containing:
- Complete PostgreSQL database dump
- `daygle_mail_archiver.conf` file with all encryption keys
- Backup metadata

**Important:** Store backups securely - they contain sensitive encryption keys and all email data.

### Listing Available Backups

```bash
./scripts/backup_restore.sh list
```

Shows all available backups with size and creation date.

### Restoring from Backup

```bash
./scripts/backup_restore.sh restore daygle_mail_archiver_backup_20240105_120000.tar.gz
```

This will:
1. Extract the backup archive
2. Restore the `daygle_mail_archiver.conf` file (backing up the current one)
3. Drop and recreate the database
4. Restore all data from the backup

**Important:** After restore, restart the services:
```bash
docker compose restart
```

### Deleting Old Backups

```bash
./scripts/backup_restore.sh delete daygle_mail_archiver_backup_20240105_120000.tar.gz
```

### Script Features

- **Complete Backup**: Includes database AND configuration file in one archive
- **Standard INI Format**: Uses daygle_mail_archiver.conf for better readability
- **Atomic Operations**: Ensures backup consistency
- **Safety Checks**: Confirms destructive operations before proceeding
- **Progress Logging**: Clear status messages during backup/restore
- **Metadata Tracking**: Each backup includes creation timestamp and contents

---

# Customizing Your Dashboard

The dashboard supports drag-and-drop widget customization to suit your workflow.

## Rearranging Widgets

1. Navigate to **Dashboard** (default page after login)
2. **Drag** any widget by its title bar to reposition it
3. **Resize** widgets by dragging the bottom-right corner
4. Changes are automatically saved to your user preferences

## Available Widgets

- **Email Statistics**: Total emails, database size
- **Storage Trends**: Email growth over time
- **Top Senders/Recipients**: Most frequent correspondents
- **Recent Activity**: Latest system events
- **Deletion Analytics**: Manual vs retention deletions
- **Account Health**: Fetch account status
- **System Status**: Overall system health

Your layout preferences are saved per-user, so each administrator can customize their own view.

---

# User Management

Daygle Mail Archiver supports multiple users with role-based access control.

## User Roles

- **Administrator**: Full access to all features including settings, user management, fetch accounts, and audit logs
- **Viewer**: Read-only access to view and search archived emails

## Managing Users

**Note:** Only administrators can manage users.

1. Navigate to **User Management** from the sidebar (Administration section)
2. Click **"Create New User"** to add a new user
3. Fill in the username, email address, and select a role
4. The new user will need to set their password on first login

## Managing Your Profile

All users can manage their own profile settings:

1. Click on your username in the top-right corner
2. Select **"Profile"** from the dropdown menu
3. Update your personal information or change your password
4. Click **"Save"** to apply changes

Users can also customize their dashboard layout and user preferences through the **"User Settings"** option.

---

# Audit Logs

The system maintains a complete audit trail of all actions for security and compliance.

## Viewing Audit Logs

**Note:** Only administrators can view audit logs.

1. Navigate to **Logs** from the sidebar (Administration section)
2. Browse the log entries showing all system events
3. Use search and filter options to find specific events

## Logged Events

- User login and logout events
- Email deletions (both manual and automated retention cleanup)
- Fetch account creation, modification, and deletion
- Settings changes
- User management actions (create, update, delete)
- OAuth authorization events
- Worker status and errors

Each log entry includes:
- **Timestamp**: When the action occurred
- **Username**: Who performed the action
- **Action Type**: Category of the action
- **Details**: Specific information about what was changed

---

# Additional Features

- **Dashboard**: View email statistics, charts, and account status (default page after login)
- **Help**: Access the built-in help page from the user menu for detailed instructions
- **Donate**: Support the project via PayPal (link in user menu and sidebar)  

---

# Viewing Logs

## Application Logs (Docker)

View container logs for troubleshooting and monitoring:

**Worker Logs** (email fetching process):
```
docker compose logs -f worker
```

**API Logs** (web application):
```
docker compose logs -f api
```

**All Services**:
```
docker compose logs -f
```

## Audit Logs (Web UI)

For viewing user activity and system events, see the **Audit Logs** section above. Administrators can access audit logs through the web interface at **Logs** in the sidebar.

---

# Resetting the System

```
docker compose down --volumes
docker compose up -d --build
```

---

# Inspecting the Database

```
docker compose exec db psql -U "$DB_USER" -d "$DB_NAME"
```

---

# Security Notes

The system implements several security measures:

- **Password Encryption**: IMAP account passwords are encrypted using Fernet (symmetric encryption) with a key stored in `IMAP_PASSWORD_KEY`
- **User Authentication**: User passwords are hashed with bcrypt before storage
- **Session Security**: Session cookies use a secret key (`SESSION_SECRET`) with 24-hour expiration
- **OAuth2 Tokens**: Gmail and Office 365 refresh tokens are stored encrypted in the database
- **Virus Scanning**: Integrated ClamAV scans incoming emails for malware and viruses
- **Compressed Storage**: Raw emails are stored compressed in PostgreSQL BYTEA columns
- **No Filesystem Storage**: All email data is stored in the database only, not on the filesystem
- **Security Headers**: API includes security headers (X-Content-Type-Options, X-Frame-Options, X-XSS-Protection)
- **Audit Logging**: All system actions are logged with timestamps and user information

**Important Security Recommendations:**
- Change default `SESSION_SECRET` and `IMAP_PASSWORD_KEY` values in production
- Enable virus scanning to protect against malware in incoming emails
- Use HTTPS in production (set `https_only=True` in session middleware)
- Regularly backup your database (encrypted backups recommended)
- Keep PostgreSQL access restricted to Docker network only
- Review audit logs regularly for suspicious activity  

---

# Troubleshooting

## Worker Not Fetching Emails

1. Check worker logs: `docker compose logs -f worker`
2. Verify fetch account is enabled in the UI
3. Check Worker Status page for error messages
4. For OAuth accounts (Gmail/O365), verify tokens are valid and re-authorize if needed
5. For IMAP accounts, test connection using the "Test Connection" button

## Cannot Login

1. If this is your first time accessing the system, you'll be redirected to the setup wizard at `/setup`
2. Complete the setup wizard to create your administrator account
3. If you forgot your password after setup, reset it directly in the database:
   ```bash
   docker compose exec db psql -U daygle_mail_archiver -d daygle_mail_archiver
   UPDATE users SET password_hash = '' WHERE username = 'your_username';
   ```
4. Then log in with empty password and set a new one via the "Set Password" page

## Database Connection Issues

1. Ensure PostgreSQL container is healthy: `docker compose ps`
2. Check database logs: `docker compose logs -f db`
3. Verify database credentials in `daygle_mail_archiver.conf` are correct

## OAuth Authorization Fails

1. Verify redirect URIs in Google Cloud Console or Azure Portal match exactly:
   - Gmail: `http://your-domain/oauth/gmail/callback/{account_id}`
   - O365: `http://your-domain/oauth/o365/callback/{account_id}`
2. Ensure client ID and client secret are correct
3. Check API logs for detailed error messages: `docker compose logs -f api`

## Emails Not Being Deleted from Mail Server

1. Verify "Delete Email After Processed" is enabled for the fetch account
2. For IMAP accounts, enable "Expunge Deleted" to permanently remove messages
3. Check worker logs for deletion errors
4. Note: OAuth accounts (Gmail/O365) move emails to Trash/Deleted Items, not permanent deletion

---

# MIT License

Copyright (c) 2026 daygle.net

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

## 🤝 Contributing

Contributions are welcome.  
Please open issues or merge requests in the GitLab project.
