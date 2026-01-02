# Daygle Mail Archiver

A small, self-contained mail archiver that:

- Connects to one or more IMAP accounts
- Downloads messages safely
- Compresses and stores raw emails in PostgreSQL
- Provides a simple web UI to browse, search, and download messages
- Requires no filesystem-based mail storage

---

## Architecture

- **PostgreSQL**: single database for everything (messages, IMAP accounts, state, settings, error log)
- **API (FastAPI)**:
  - Serves the web UI
  - Renders messages, IMAP accounts, settings, error log
  - Uses the same DB as the worker
- **Worker**:
  - Polls IMAP accounts
  - Tracks last UID per folder
  - Downloads new messages
  - Compresses raw emails with gzip
  - Inserts into `messages` table
  - Updates IMAP state and error log

---

## Project layout

```text
daygle-mail-archiver/
  api/
    src/
      app.py
      routes/
      utils/
    templates/
    static/
    Dockerfile
    requirements.txt

  worker/
    src/
      worker.py
      imap_client.py
      db.py
      security.py
    Dockerfile
    requirements.txt

  db/
    schema.sql
    migrations/

  docker-compose.yml
  .env.example
  README.md
