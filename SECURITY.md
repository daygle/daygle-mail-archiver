# Security Policy

Daygle Mail Archiver is a self-hosted email archiving system that handles sensitive data: full email contents, stored credentials, and session state. We take reports about this project seriously and appreciate responsible disclosure.

## Supported versions

The project is distributed through this repository and deployed from source with Docker Compose. There are no long-term maintenance branches or pinned release lines:

- Only the **latest revision of `main`** is supported. If you are running an older checkout, update to the current `main` before reporting an issue so we are not chasing fixed bugs.
- Keep the whole stack current with `./update.sh` (pulls images, rebuilds containers, and re-applies the idempotent database schema). Review the script and take a backup with `./backup_restore.sh backup` first - it does **not** create a database backup automatically.
- ClamAV runs from the `clamav/clamav:latest` image; signature and engine updates arrive when you pull and restart the container, so refresh it regularly.

## Reporting a vulnerability

**Please do not open a public GitHub issue, discussion, or pull request for a security problem.**

Use [GitHub's private vulnerability reporting](https://github.com/daygle/daygle-mail-archiver/security/advisories/new) for this repository. This keeps the details private until a fix is available.

When reporting, please include as much of the following as you can:

- The affected component (`api/`, `worker/`, `db/schema.sql`, Docker/Compose configuration, update or backup scripts, bundled static assets)
- The revision of `main` you tested (commit SHA is ideal) and your deployment shape (direct exposure vs. reverse proxy)
- Step-by-step reproduction instructions or a proof of concept
- The impact you believe is achievable and any preconditions (role/permission required, settings enabled, etc.)

We aim to acknowledge reports within a few business days. This is a community-maintained project, so fix timelines depend on severity and complexity; we will keep you informed of progress either way.

## Safe harbor

We consider security research conducted in good faith to be authorized, provided you:

- Only test installations that you own or have explicit permission to test - never probe instances you do not control
- Avoid actions that degrade service for others (flooding, sustained load, or resource exhaustion tests)
- Use test or disposable mailboxes for any reproduction involving live email data
- Stop and report as soon as you can demonstrate the issue; do not pivot deeper than necessary
- Keep findings confidential until a fix is released and we agree on disclosure

## Scope

**In scope:**

- API and worker source under `api/` and `worker/`
- Database schema and initialization under `db/`
- The deployment configuration: `docker-compose.yml`, `daygle_mail_archiver.conf` handling, and the `update.sh` / `backup_restore.sh` scripts
- Vulnerable or outdated Python dependencies in `api/requirements.txt` and `worker/requirements.txt` (CI already runs `pip-audit` against both, so check the latest revision first)
- The bundled frontend assets under `api/static/`

**Out of scope:**

- Misconfiguration of a self-hosted deployment (unchanged default credentials or `CHANGE_ME_*` values, wildcard CORS, plain-HTTP exposure, missing firewall rules). These are deployment responsibilities covered in the hardening checklist below.
- The reverse proxy, host OS, container runtime, or the surrounding network - harden and monitor those yourself
- Reports from automated scanners without a demonstrated, reachable impact
- Denial-of-service by volume, spam, or social engineering of email senders
- Content of the public wiki and other documentation, except where a documented instruction would cause an insecure default

## Deployment hardening checklist

The application can only be as secure as its deployment. Before production use:

- Replace **every** `CHANGE_ME_*` value and the database `change_me` password in `daygle_mail_archiver.conf` (see `daygle_mail_archiver.conf.example`)
- Generate unique, high-entropy `session_secret`, `imap_password_key`, and - if quarantine encryption is enabled - a **dedicated** `clamav_quarantine_key` (do not reuse the IMAP key)
- Serve over HTTPS behind a reverse proxy and set `session_https_only = true`; set `public_base_url` so OAuth redirect URIs match exactly
- Restrict `allowed_origins` to an explicit list instead of the wildcard default
- Enable ClamAV scanning and configure email alerts so security events reach you
- Restrict access to backups: they contain email data, database credentials, and encryption keys
- Keep role assignments minimal - the RBAC system is enforced server-side, but built-in role edits affect every assigned user (see `docs/roles-and-permissions.md`)

More detail lives in `docs/configuration.md` and the [Security Notes wiki page](https://github.com/daygle/daygle-mail-archiver/wiki/Security-Notes).

## Fixes and credit

Fixes are released on `main` as soon as they are ready and verified. We are happy to credit reporters in release notes or advisory text - say so in your report and tell us the name or handle you would like used.
