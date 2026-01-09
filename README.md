# Daygle Mail Archiver

Daygle Mail Archiver is a deterministic email ingestion and archiving system designed for long‑term retention, auditability, and operational reliability. It ingests emails from multiple sources (IMAP, Gmail API, Office 365 Graph API), stores messages in a structured database, and exposes a clean UI for browsing, retention policy management, and administrative control.

This project is built with explicit, maintainable configuration, modular backend logic, and a modernized UI — ensuring predictable behaviour across all environments.

---

## ✨ Features

- **Multi-Source Email Fetching**: IMAP/IMAPS, Gmail API, Office 365 Graph API with delta sync
- **Automatic Email Archiving**: Continuously polls accounts and stores emails
- **Search & Filter**: Full-text search across subjects, senders, and recipients
- **Raw Email Storage**: Complete RFC822 format with compression
- **Retention Policies**: Automatic purging based on configurable rules
- **Deletion Tracking**: Dashboard analytics for manual and automated deletions
- **Mail Server Cleanup**: Optional deletion from mail servers during retention cleanup
- **User Management**: Multi-user system with role-based access (Administrator/Read Only)
- **OAuth2 Integration**: Secure authentication for Gmail and Office 365
- **Worker Status Monitoring**: Real-time health monitoring of fetch workers
- **Dashboard Analytics**: Visual charts and customizable widget layouts
- **Test Connection**: Test IMAP, Gmail, and Office 365 connections from the UI
- **Database Backup & Restore**: Built-in backup functionality
- **Audit Logging**: Complete audit trail of all system actions
- **Virus Scanning**: Integrated ClamAV for scanning incoming emails with configurable actions
- **Advanced Reporting**: Email volume trends, account activity, user analytics, and system health reports
- **Email Alerts**: Configurable SMTP alerts for system events, virus detections, and critical issues
- **Alert Management**: Real-time alert dashboard with acknowledgment and email notifications

---

## 📖 Documentation

**Complete documentation is available in the [Wiki](https://github.com/daygle/daygle-mail-archiver/wiki/)**

### Quick Links

- **[Installation Guide](https://github.com/daygle/daygle-mail-archiver/wiki/Installation-Guide)** - Get started with installation
- **[Configuration](https://github.com/daygle/daygle-mail-archiver/wiki/Configuration)** - Configure the system
- **[Email Accounts Setup](https://github.com/daygle/daygle-mail-archiver/wiki/Email-Accounts-Setup)** - Set up IMAP, Gmail, Office 365
- **[User Management](https://github.com/daygle/daygle-mail-archiver/wiki/User-Management)** - Manage users and roles
- **[Dashboard Customization](https://github.com/daygle/daygle-mail-archiver/wiki/Dashboard-Customization)** - Customize your dashboard
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

## 🛠️ Development Setup

For developers who want to contribute or run the application locally without Docker:

### Prerequisites

- Python 3.12 or higher
- pip (Python package manager)
- Git

### Local Development Installation

```bash
# Clone repository
git clone https://github.com/daygle/daygle-mail-archiver.git
cd daygle-mail-archiver

# Set up Python virtual environment
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/Mac:
source .venv/bin/activate

# Install dependencies
cd api
pip install -r requirements.txt

# Set up environment variables
cd ..
cp .env.example .env
# Edit .env with your configuration

# Initialize database
python init_db.py

# Run development server
python dev.py
```

The application will be available at `http://localhost:8000`

### Development Features

- **Auto-reload**: Code changes automatically restart the server
- **SQLite database**: Lightweight database for development
- **Environment variables**: Configuration via `.env` file
- **Debug logging**: Detailed logs for troubleshooting

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

Reports are accessible via the **Reports** menu and support customizable date ranges and export capabilities.

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

## �🔄 Updating

Check for updates from the dashboard or via command line:

```bash
# Check for updates
./update.sh --check

# Update system
./update.sh
```

The update script automatically backs up data before updating.

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
