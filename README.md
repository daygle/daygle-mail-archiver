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

---

## 📖 Documentation

**Complete documentation is available in the [Wiki](https://github.com/daygle/daygle-mail-archiver/wiki/)**

### Quick Links

- **[Installation Guide](docs/wiki/Installation-Guide.md)** - Get started with installation
- **[Configuration](docs/wiki/Configuration.md)** - Configure the system
- **[Email Accounts Setup](docs/wiki/Email-Accounts-Setup.md)** - Set up IMAP, Gmail, Office 365
- **[User Management](docs/wiki/User-Management.md)** - Manage users and roles
- **[Dashboard Customization](docs/wiki/Dashboard-Customization.md)** - Customize your dashboard
- **[ClamAV Virus Scanning](docs/wiki/ClamAV-Virus-Scanning.md)** - Configure virus scanning
- **[Backup and Restore](docs/wiki/Backup-and-Restore.md)** - Backup and restore procedures
- **[Troubleshooting](docs/wiki/Troubleshooting.md)** - Common issues and solutions
- **[Security Notes](docs/wiki/Security-Notes.md)** - Security best practices

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
git clone https://gitlab.com/daygle/daygle-mail-archiver.git
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

See the [Installation Guide](docs/wiki/Installation-Guide.md) for detailed instructions.

---

## 🏗️ Architecture

Four main components:

1. **PostgreSQL Database** - Stores emails, accounts, users, settings
2. **FastAPI Web Application** - Web UI and REST API (port 8000)
3. **Background Worker** - Email fetching and retention cleanup
4. **ClamAV** - Virus scanning service

All components run in Docker containers orchestrated by Docker Compose.

---

## 🔄 Updating

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

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

---

## 📄 License

This project is licensed under the MIT License. See the LICENSE file for details.

---

## 🆘 Support

- **Documentation**: [Wiki](docs/wiki/Home.md)
- **Issues**: [GitHub Issues](https://github.com/daygle/daygle-mail-archiver/issues)
- **Troubleshooting**: [Troubleshooting Guide](docs/wiki/Troubleshooting.md)

---

## ⚠️ Security

**Before production deployment:**
- Change all default passwords and secrets
- Enable HTTPS via reverse proxy
- Configure firewall rules
- Enable virus scanning
- Set up regular backups

See [Security Notes](docs/wiki/Security-Notes.md) for complete security guidelines.
