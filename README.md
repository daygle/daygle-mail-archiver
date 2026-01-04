# Daygle Mail Archiver

Daygle Mail Archiver is a deterministic, scenario‑proof email ingestion and archiving system designed for long‑term retention, auditability, and operational reliability. It ingests emails from multiple sources (IMAP, Gmail API, Office 365 Graph API), decrypts OpenPGP‑protected content when keys are available, stores messages in a structured database, and exposes a clean UI for browsing, retention policy management, and administrative control.

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
- **User Management**: Multi-user system with role-based access
- **OAuth2 Integration**: Secure authentication for Gmail and Office 365
- **Worker Status Monitoring**: Real-time health monitoring of fetch workers
- **Dashboard Analytics**: Visual charts showing email statistics and trends
- **Audit Logging**: Complete audit trail of all system actions

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

---

## 3. Build and start the system

```
docker compose up -d --build
```

This will:

- Start PostgreSQL  
- Apply `db/schema.sql`  
- Start the API on port 8000  
- Start the worker  

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
- Password: `administrator`  

---

# Adding IMAP Accounts

1. Go to **IMAP Accounts**  
2. Click **Add New Account**  
3. Fill in:
   - Name  
   - Host  
   - Port  
   - Username  
   - Password  
   - SSL / STARTTLS  
   - Poll interval  
   - Enabled  
4. Save  

The worker will:

- decrypt password  
- connect to IMAP  
- iterate folders  
- fetch new messages  
- compress and store them  
- update IMAP state  

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

# Worker Status Monitoring

Monitor the health and activity of email fetch workers:

1. Navigate to **Settings → Worker Status**
2. View real-time status for each account:
   - **Health Status**: Healthy, Stale, Error, Pending, or Disabled
   - **Last Heartbeat**: When the worker last checked the account
   - **Last Success**: When emails were last successfully fetched
   - **Last Error**: Any error messages from failed attempts
3. Use this page to troubleshoot connection issues or OAuth problems

---

# Browsing Emails

Go to:

```
/emails
```

You can:

- search  
- filter  
- view message details  
- view HTML or text body  
- download `.eml`  

---

# Worker Logs

```
docker compose logs -f worker
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

- IMAP passwords encrypted with Fernet  
- Raw emails stored compressed in PostgreSQL  
- No filesystem mail storage  
- Admin login controlled via `.env`  

---

## 📄 License

MIT (or your preferred license)

---

## 🤝 Contributing

Contributions are welcome.  
Please open issues or merge requests in the GitLab project.
```
