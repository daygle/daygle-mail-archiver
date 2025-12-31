# Daygle Mail Archiver

A lightweight, Docker-based IMAP mail archiver designed for deterministic, scenario-proof ingestion.  
Daygle fetches emails from IMAP, stores the raw .eml files, extracts metadata into Postgres, and optionally deletes messages after successful processing.

---

## 🚀 Features

- IMAP ingestion worker (Python)
- Safe, idempotent processing (never deletes until archived)
- Raw .eml storage on filesystem
- Metadata stored in Postgres
- Optional FastAPI API for browsing/searching
- Fully containerized with Docker Compose
- Designed for reliability, clarity, and maintainability

---

## 📦 Requirements

Before installing, ensure you have:

- Docker (20+ recommended)
- Docker Compose (v2+)
- Git

---

## 📥 Installation

### 1. Clone the repository

    git clone https://gitlab.com/daygle/daygle-mail-archiver.git
    cd daygle-mail-archiver

---

### 2. Create your environment file

Copy the example:

    cp .env.example .env

Edit .env and set your IMAP + DB credentials:

    DB_DSN=postgres://daygle:change_me@db:5432/daygle

    IMAP_HOST=imap.example.com
    IMAP_PORT=993
    IMAP_USER=user@example.com
    IMAP_PASSWORD=change_me
    IMAP_USE_SSL=true

    POLL_INTERVAL_SECONDS=300
    DELETE_AFTER_PROCESSING=true
    STORAGE_DIR=/data/mail

---

### 3. Build and start the stack

    docker compose up -d --build

This will start:

- Postgres
- Worker (IMAP ingestion loop)
- API (optional, on port 8080)

---

### 4. Verify the worker is running

    docker compose logs -f worker

You should see logs like:

    Connected to IMAP
    Processing UID 1234
    Stored message at /data/mail/default/INBOX/1234.eml
    Deleted from IMAP

---

## 📂 Directory Structure

    daygle-mail-archiver/
    ├── docker-compose.yml
    ├── .env
    ├── worker/
    ├── api/
    └── docs/

Raw emails are stored in the Docker volume:

    /data/mail/<account>/<folder>/<uid>.eml

---

## 🌐 API Usage (Optional)

Once running, visit:

    http://localhost:8080/messages

This returns the latest archived messages.

---

## 🔄 Updating the system

Pull the latest changes:

    git pull
    docker compose up -d --build

---

## 🧪 Development Mode

If you want to run the worker locally without Docker:

    cd worker
    pip install -r requirements.txt
    python main.py

Make sure your .env variables are exported or use a .env loader.

---

## 🛠 Roadmap

See docs/roadmap.md for planned features, including:

- Multi-account support
- Search indexing
- Web UI
- Retention policies

---

## 📜 License

MIT (or whichever you choose — update this section to your actual license)