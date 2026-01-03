# Daygle Mail Archiver

Daygle Mail Archiver is a deterministic, scenario‑proof email ingestion and archiving system designed for long‑term retention, auditability, and operational reliability. It ingests messages from IMAP/POP3 sources, decrypts OpenPGP‑protected content when keys are available, stores messages in a structured database, and exposes a clean UI for browsing, retention policy management, and administrative control.

This project is built with explicit, maintainable configuration, modular backend logic, and a modernized UI — ensuring predictable behaviour across all environments.

---

## ✨ Features

### **Email Ingestion**
- Deterministic IMAP/POP3 polling  
- Duplicate‑safe ingestion with pointer tracking  
- OpenPGP decryption when recipient keys are available  
- Full message + attachment extraction  

### **Retention Policy Engine**
- Configurable retention windows  
- “Preview purge” mode  
- “Purge now” execution  
- Last‑run timestamps for auditability  
- Deterministic deletion rules based on `created_at`  

### **Modern UI**
- Clean, responsive interface  
- Mail browser with message + attachment viewer  
- Admin panel for retention, credentials, and system state  
- Explicit error feedback and validation  

---

# Setup & Installation

Follow these steps to get a fully running Daygle instance.

---

## 1. Clone the repository

```
cd /opt/
git clone https://gitlab.com/daygle/daygle-mail-archiver
cd daygle-mail-archiver
```

---

## 2. Create your `.env` file

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

# Browsing Messages

Go to:

```
/messages
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
