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

---

## 🏗️ Architecture

The Daygle Mail Archiver consists of three main components:

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
   - Updates heartbeat and health status

All components run in Docker containers orchestrated by Docker Compose.

---

# Setup & Installation

Follow these steps to get a fully running Daygle instance.

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

```
cp .env.example .env
```

Your `.env` should look like this:

```
DB_NAME=daygle_mail_archiver
DB_USER=daygle_mail_archiver
DB_PASS=change_me

POSTGRES_DB=${DB_NAME}
POSTGRES_USER=${DB_USER}
POSTGRES_PASSWORD=${DB_PASS}

DB_DSN=postgresql+psycopg2://${DB_USER}:${DB_PASS}@db:5432/${DB_NAME}

SESSION_SECRET=8f4c2b9e3d7a4f1c9e8b2d3f7c6a1e4b5d8f0c2a7b9d3e6f1a4c7b8d9e2f3a1

IMAP_PASSWORD_KEY=8t2y0x8qZp8G7QfVYp4p0Q2u7v8Yx1m4l8e0q2c3s0A=
```

**Important:** Change the default values for `DB_PASS`, `SESSION_SECRET`, and `IMAP_PASSWORD_KEY` in production!

To generate a new Fernet key for `IMAP_PASSWORD_KEY`:
```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

To generate a new session secret:
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
- Create default administrator user (username: `administrator`, no password set)
- Start the API container on port 8000 (`daygle-mail-archiver-api`)
- Start the worker container (`daygle-mail-archiver-worker`)  

---

## 4. Verify containers are healthy

```
docker compose ps
```

Expected:

- `daygle-mail-archiver-database` → healthy  
- `daygle-mail-archiver-api` → running  
- `daygle-mail-archiver-worker` → running  

---

## 5. Access the web UI

Open:

```
http://localhost:8000/login
```

Login:

- Username: `administrator`
- Password: (empty - you'll be prompted to set a password on first login)

After setting your password, you'll be redirected to the Dashboard.  

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

# Database Backup & Restore

Protect your email archive with built-in database backup and restore functionality.

## Creating a Backup

1. Navigate to **Settings → Backup/Restore** (from the sidebar menu)
2. Click **Download Backup**
3. The system will create a complete PostgreSQL dump and download it as `daygle_backup.sql`
4. Store this file securely for disaster recovery

**Notes:**
- Backup includes all emails, accounts, users, settings, and logs
- Maximum backup time: 60 seconds (for large databases, consider manual pg_dump)
- Backup files are plain-text SQL format

## Restoring from Backup

1. Navigate to **Settings → Backup/Restore**
2. Click **Choose File** and select your backup `.sql` file
3. Click **Restore Database**
4. The system will restore all data from the backup

**Important Warnings:**
- Restore will overwrite all existing data in the database
- Maximum file size: 10MB (for larger restores, use manual psql)
- Maximum restore time: 120 seconds
- Always test restores on a non-production system first
- You may need to log in again after a restore

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

# Additional Features

- **Dashboard**: View email statistics, charts, and account status (default page after login)
- **Help**: Access the built-in help page from the user menu for detailed instructions
- **Donate**: Support the project via PayPal (link in user menu and sidebar)  

---

# Viewing Logs

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
- **Compressed Storage**: Raw emails are stored compressed in PostgreSQL BYTEA columns
- **No Filesystem Storage**: All email data is stored in the database only, not on the filesystem
- **Security Headers**: API includes security headers (X-Content-Type-Options, X-Frame-Options, X-XSS-Protection)
- **Audit Logging**: All system actions are logged with timestamps and user information

**Important Security Recommendations:**
- Change default `SESSION_SECRET` and `IMAP_PASSWORD_KEY` values in production
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

1. For first login with `administrator` user, use empty password and set a new one
2. If you forgot your password, reset it directly in the database:
   ```bash
   docker compose exec db psql -U daygle_mail_archiver -d daygle_mail_archiver
   UPDATE users SET password_hash = '' WHERE username = 'administrator';
   ```
3. Then log in with empty password and set a new one

## Database Connection Issues

1. Ensure PostgreSQL container is healthy: `docker compose ps`
2. Check database logs: `docker compose logs -f db`
3. Verify `DB_DSN` in `.env` matches your database credentials

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

## 📄 License

MIT (or your preferred license)

---

## 🤝 Contributing

Contributions are welcome.  
Please open issues or merge requests in the GitLab project.
