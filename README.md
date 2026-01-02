# Daygle Mail Archiver

A clean, deterministic, self‑contained mail archiver designed for long‑term retention, auditability, and reliability.  
Daygle connects to IMAP accounts, downloads messages, compresses them, stores them in PostgreSQL, and provides a modern web UI for browsing, searching, and downloading archived mail.

---

## Features

- IMAP ingestion worker  
- Tracks last UID per folder  
- Gzip‑compresses raw emails  
- Stores messages in PostgreSQL  
- Modern FastAPI web UI  
- Full‑text search  
- Download `.eml` files  
- Error log viewer  
- Docker‑based deployment  

---

## Project Structure

```
daygle-mail-archiver/
  api/
  worker/
  db/
  docker-compose.yml
  .env.example
  README.md
```

---

# Setup & Installation

Follow these steps to get a fully running Daygle instance.

---

## 1. Clone the repository

```
cd /opt/
git clone https://github.com/your/repo.git daygle-mail-archiver
cd daygle-mail-archiver
```

---

## 2. Create your `.env` file

```
cp .env.example .env
```

Your `.env` should look like this:

```
# PostgreSQL Credentials
DB_NAME=daygle_mail_archiver
DB_USER=daygle_mail_archiver
DB_PASS=change_me

# PostgreSQL Container Config
POSTGRES_DB=${DB_NAME}
POSTGRES_USER=${DB_USER}
POSTGRES_PASSWORD=${DB_PASS}

# Database DSN (used by API + Worker)
DB_DSN=postgresql+psycopg2://${DB_USER}:${DB_PASS}@db:5432/${DB_NAME}

# API Session
SESSION_SECRET=8f4c2b9e3d7a4f1c9e8b2d3f7c6a1e4b5d8f0c2a7b9d3e6f1a4c7b8d9e2f3a1

# IMAP Password Encryption
IMAP_PASSWORD_KEY=8t2y0x8qZp8G7QfVYp4p0Q2u7v8Yx1m4l8e0q2c3s0A=

# Admin Login
ADMIN_USERNAME=administrator
ADMIN_PASSWORD=administrator
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

- `daygle_db` → healthy  
- `daygle_api` → running  
- `daygle_worker` → running  

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

Useful queries:

```
SELECT COUNT(*) FROM messages;
SELECT * FROM imap_accounts;
SELECT * FROM error_log ORDER BY timestamp DESC LIMIT 20;
```

---

# Security Notes

- IMAP passwords encrypted with Fernet  
- Raw emails stored compressed in PostgreSQL  
- No filesystem mail storage  
- Admin login controlled via `.env`  

---

# License

MIT (or your preferred license)
