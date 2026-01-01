# Daygle Mail Archiver

A deterministic, scenario‑proof IMAP mail archiver built for reliability, clarity, and maintainability.  
Daygle ingests emails from one or more IMAP accounts, stores raw `.eml` files, extracts metadata into Postgres, and provides a full web UI for browsing, searching, and monitoring.

---

## 🚀 Features

- **Multi‑account IMAP ingestion** (one worker per IMAP account)
- **Encrypted IMAP passwords** stored securely in Postgres
- **Raw `.eml` storage** on filesystem
- **Metadata indexing** in Postgres
- **FastAPI web UI** for browsing, searching, filtering
- **Per‑account worker heartbeat + error logging**
- **Status dashboard** (storage, DB health, worker status)
- **Settings UI** for global configuration
- **Accounts UI** for adding/editing IMAP accounts
- Fully containerized with **Docker Compose**

---

## 📦 Requirements

You need:

- Docker 20+
- Docker Compose v2+
- Git

---

## 📥 Installation

### 1. Clone the repository

```bash
git clone https://gitlab.com/daygle/daygle-mail-archiver.git
cd daygle-mail-archiver
```

---

### 2. Create your environment file

Copy the example:

```bash
cp .env.example .env
```

Edit `.env` and set the global settings:

```
DB_DSN=postgres://daygle:change_me@db:5432/daygle
IMAP_PASSWORD_KEY=generate_a_32byte_key
SESSION_SECRET=change_me
STORAGE_DIR=/data/mail
```

**Important:**  
IMAP settings are *not* stored in `.env`.  
You will configure IMAP accounts through the **web UI** after installation.

---

### 3. Build and start the stack

```bash
docker compose up -d --build
```

This starts:

- **db** – Postgres (auto‑initializes schema)
- **api** – Web UI (port 8080)
- **worker_default** – Worker for the default IMAP account  
  (You can add more workers later)

---

### 4. Open the web UI

Visit:

```
http://localhost:8080
```

Login with the default admin account:

- **Username:** `administrator`
- **Password:** `administrator`

You can change this later.

---

### 5. Add IMAP accounts

Go to:

```
Accounts → Add Account
```

Add one or more accounts:

- Name (identifier)
- Host / Port
- Username
- Password (encrypted automatically)
- SSL / STARTTLS
- Poll interval
- Delete‑after‑processing
- Enabled/disabled

Each account you add will require a worker container.

---

### 6. Start workers for each account

Each worker container needs:

```
IMAP_ACCOUNT_NAME=<account_name>
```

Example in `docker-compose.yml`:

```yaml
worker_default:
  build: ./worker
  environment:
    DB_DSN: ${DB_DSN}
    IMAP_PASSWORD_KEY: ${IMAP_PASSWORD_KEY}
    IMAP_ACCOUNT_NAME: default
  depends_on:
    - db
```

For a second account:

```yaml
worker_work:
  build: ./worker
  environment:
    DB_DSN: ${DB_DSN}
    IMAP_PASSWORD_KEY: ${IMAP_PASSWORD_KEY}
    IMAP_ACCOUNT_NAME: work
  depends_on:
    - db
```

Then run:

```bash
docker compose up -d --build
```

---

## 📂 Directory Structure

```
daygle-mail-archiver/
├── docker-compose.yml
├── .env
├── worker/
├── api/
└── db/
```

Raw emails are stored in:

```
/data/mail/<account>-<uid>.eml
```

---

## 🌐 Web UI

### Messages
```
http://localhost:8080/messages
```

Search, filter, view metadata, view body text.

### Accounts
```
http://localhost:8080/accounts
```

Add/edit IMAP accounts, test IMAP connectivity.

### Status Dashboard
```
http://localhost:8080/status
```

Shows:

- Per‑account worker heartbeat
- Last success/error
- Storage usage
- DB health

### Error Log
```
http://localhost:8080/errors
```

Shows recent worker/API/storage errors.

---

## 🔄 Updating the system

```bash
git pull
docker compose up -d --build
```

Workers will restart automatically.

---

## 🧪 Development Mode

To run the worker locally:

```bash
cd worker
pip install -r requirements.txt
export IMAP_ACCOUNT_NAME=default
python main.py
```

To run the API locally:

```bash
cd api
pip install -r requirements.txt
uvicorn app:app --reload --port 8080
```

---

## 🛠 Roadmap

- Per‑folder selection per account  
- Attachment indexing  
- Full‑text search improvements  
- Retention policies  
- Export tools  

---

## 📜 License

MIT (or your chosen license)