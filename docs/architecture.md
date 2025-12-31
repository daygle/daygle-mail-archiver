# Daygle Mail Archiver — Architecture

## Components
- IMAP ingestion worker
- Postgres metadata store
- Filesystem storage for raw .eml files
- Optional FastAPI UI/API

## Flow
1. Worker connects to IMAP
2. Fetches messages
3. Hashes + stores raw email
4. Inserts metadata into DB
5. Deletes from IMAP (optional)