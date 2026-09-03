from typing import List
import io
import os
import gzip
import tempfile
import urllib.parse
import zipfile
import mailbox
from email import message_from_bytes
from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse, StreamingResponse, HTMLResponse
from imaplib import IMAP4, IMAP4_SSL
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..utils.db import query, execute, transaction
from ..utils.email_parser import decompress, parse_email, compute_signature, get_attachment_parts
from ..utils.crypto import decrypt_password
from ..utils.logger import log
from ..utils.templates import templates
from ..utils.timezone import format_datetime, format_email_date, user_date_to_utc_range_start, user_date_to_utc_range_end
from ..utils.alerts import create_alert
from ..utils.permissions import PermissionChecker, require_permission, PERMISSIONS
from ..utils.clamav_scanner import ClamAVScanner

router = APIRouter()

# Shared scanner for the import path. Constructing a ClamAVScanner per email
# would run a settings DB query and open a clamd connection for every message
# in a large mbox/zip import; caching a singleton mirrors the worker.
_import_scanner = None


def _get_import_scanner() -> ClamAVScanner:
    global _import_scanner
    if _import_scanner is None:
        _import_scanner = ClamAVScanner()
    return _import_scanner


def _scanner_requires_scan(scanner) -> bool:
    """Return whether the scanner is configured to perform a scan.

    The fallback keeps this helper compatible with small test doubles and older
    scanner implementations while the production scanner exposes requires_scan.
    """
    requires_scan = getattr(scanner, "requires_scan", None)
    return requires_scan() if callable(requires_scan) else scanner.is_enabled()


def _scan_email_ids(ids: List[int], username: str) -> dict:
    """Scan archived emails and persist the result for each eligible row.

    Scanning is deliberately separate from quarantine: a manual scan updates
    the email's ClamAV metadata, while the user can review the result and use
    the existing quarantine action if a threat is found. It never silently
    deletes or moves an existing archived email, even when a virus is found.
    """
    result = {"scanned": 0, "clean": 0, "infected": 0, "skipped": 0, "errors": []}
    if not ids:
        return result

    try:
        scanner = _get_import_scanner()
        if not _scanner_requires_scan(scanner):
            result["errors"].append("ClamAV scanning is disabled in Global Settings.")
            return result
    except Exception as exc:
        result["errors"].append(f"ClamAV scanner is unavailable: {exc}")
        return result

    for email_id in ids:
        try:
            row = query(
                """
                SELECT id, raw_email, compressed, signature, quarantined
                FROM emails
                WHERE id = :id
                """,
                {"id": email_id},
            ).mappings().first()
        except Exception as exc:
            result["skipped"] += 1
            result["errors"].append(f"Email {email_id} could not be loaded: {exc}")
            continue
        if not row:
            result["skipped"] += 1
            result["errors"].append(f"Email {email_id} was not found.")
            continue
        if row.get("quarantined"):
            result["skipped"] += 1
            result["errors"].append(f"Email {email_id} is already quarantined.")
            continue
        if row.get("raw_email") is None:
            result["skipped"] += 1
            result["errors"].append(f"Email {email_id} has no raw email data to scan.")
            continue

        try:
            raw = decompress(row["raw_email"], row.get("compressed", False))
            original_signature = row.get("signature")
            original_raw = row["raw_email"]
            original_compressed = row.get("compressed", False)
            detected, virus_name, scan_timestamp, scanned = scanner.scan(raw)
        except Exception as exc:
            result["skipped"] += 1
            result["errors"].append(f"Email {email_id} could not be scanned: {exc}")
            continue

        if not scanned or scan_timestamp is None:
            result["skipped"] += 1
            result["errors"].append(f"Email {email_id}: ClamAV did not complete the scan.")
            continue

        try:
            updated = execute(
                """
                UPDATE emails
                SET virus_scanned = :virus_scanned,
                    virus_detected = :virus_detected,
                    virus_name = :virus_name,
                    scan_timestamp = :scan_timestamp
                WHERE id = :id
                  AND quarantined = FALSE
                  AND signature IS NOT DISTINCT FROM :original_signature
                  AND raw_email IS NOT DISTINCT FROM :original_raw
                  AND compressed IS NOT DISTINCT FROM :original_compressed
                """,
                {
                    "id": email_id,
                    "original_signature": original_signature,
                    "original_raw": original_raw,
                    "original_compressed": original_compressed,
                    "virus_scanned": True,
                    "virus_detected": bool(detected),
                    "virus_name": virus_name if detected else None,
                    "scan_timestamp": scan_timestamp,
                },
            )
        except Exception as exc:
            result["skipped"] += 1
            result["errors"].append(f"Email {email_id} scan result could not be saved: {exc}")
            continue
        if getattr(updated, "rowcount", 1) == 0:
            result["skipped"] += 1
            result["errors"].append(f"Email {email_id} changed before its scan result could be saved.")
            continue

        result["scanned"] += 1
        if detected:
            result["infected"] += 1
            log(
                "warning",
                "ClamAV",
                f"User '{username}' manually scanned infected email {email_id}: {virus_name or 'Unknown'}",
                f"Email ID: {email_id}, virus: {virus_name or 'Unknown'}",
            )
            try:
                create_alert(
                    "error",
                    "Virus Detected During Manual Scan",
                    f"ClamAV detected {virus_name or 'an unknown threat'} in email {email_id}.",
                    f"User: {username}\nEmail ID: {email_id}\nVirus: {virus_name or 'Unknown'}",
                    trigger_key="virus_detected",
                )
            except Exception as exc:
                log("error", "ClamAV", f"Failed to create manual scan alert: {exc}", "")
        else:
            result["clean"] += 1

    return result


def require_login(request: Request):
    return "user_id" in request.session


def flash(request: Request, message: str, category: str = 'info'):
    request.session["flash"] = {"message": message, "type": category}


VALID_EMAIL_FILTERS = {
    "missing_subject",
    "missing_sender",
    "missing_recipients",
    "unscanned",
    "virus_detected",
}

# Upper bound for a single uploaded import file (bytes). Prevents an upload
# from being read fully into memory and exhausting the process.
MAX_IMPORT_FILE_SIZE = 100 * 1024 * 1024

# Cap on the number of per-file issues reported in the import flash message.
MAX_IMPORT_ERRORS_SHOWN = 20


def _iter_mbox_messages(content: bytes):
    """Yield the raw RFC822 bytes of each message inside an mbox file.

    ``mailbox.mbox`` requires a real filesystem path (it calls
    ``os.path.expanduser`` on its argument), so the uploaded bytes are spooled
    to a temporary file first. Iterating the parser - instead of splitting the
    bytes on ``b"\nFrom "`` - also handles bodies whose lines start with
    "From " (the mbox From_-escaping rule), skips file preambles, and handles
    empty files.

    A non-empty file containing no "From " envelope line at all (e.g. a bare
    .eml renamed to .mbox) is treated as a single message, preserving the
    behaviour of the old manual parser rather than silently importing nothing.
    """
    fd, path = tempfile.mkstemp(prefix="daygle-import-", suffix=".mbox")
    yielded_any = False
    try:
        with os.fdopen(fd, "wb") as tmp:
            tmp.write(content)
        mbox = mailbox.mbox(path, create=False)
        try:
            for index, message in enumerate(mbox):
                try:
                    raw = message.as_bytes()
                except Exception as e:
                    log("error", "Import", f"Failed to read mbox message {index}: {str(e)}", "")
                    continue
                if raw:
                    yielded_any = True
                    yield raw
        finally:
            try:
                mbox.close()
            except Exception:
                pass
        if not yielded_any and content.strip():
            yield content
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _record_insert_result(status: str, label: str) -> tuple:
    """Translate an ``_insert_raw_email`` status into import counters.

    Returns ``(imported, rejected, error_message)`` - one of the first two is
    1 on success/rejection, and ``error_message`` is set on failure so callers
    don't each re-implement the tri-state dispatch.
    """
    if status == "ok":
        return 1, 0, None
    if status == "rejected":
        return 0, 1, None
    return 0, 0, f"{label}: failed to insert"


def _build_import_flash(imported: int, rejected: int, errors: list) -> tuple:
    """Build the (message, category) flash describing an import result.

    Success and per-file issues are combined into a single message (the session
    flash is a single slot, so two flash() calls would overwrite each other),
    the error list is capped, and virus rejections are reported separately from
    hard failures.
    """
    if imported > 0 or rejected > 0 or errors:
        parts = []
        if imported > 0:
            parts.append(f"Imported {imported} message(s).")
        if rejected > 0:
            parts.append(f"{rejected} message(s) blocked (virus detected).")
        if errors:
            shown = errors[:MAX_IMPORT_ERRORS_SHOWN]
            if len(errors) > MAX_IMPORT_ERRORS_SHOWN:
                shown.append(f"... and {len(errors) - MAX_IMPORT_ERRORS_SHOWN} more issue(s)")
            issues = "Issues: " + "; ".join(shown)
            if imported == 0 and rejected == 0:
                issues = "No messages were imported. " + issues
            parts.append(issues)
        # Anything blocked/failed is surfaced with a non-green flash; pure
        # success is the only path to a green alert.
        category = "error" if errors else ("warning" if rejected else "success")
        return " ".join(parts), category
    return "No messages were imported.", "info"


def _quote_imap_folder(folder: str) -> str:
    """Quote an IMAP mailbox name when it needs escaping for the protocol.

    imaplib does not quote arguments itself, so a folder containing spaces
    (e.g. "Sent Items") would be sent as ``SELECT Sent Items`` and rejected
    by the server.
    """
    if any(ch in folder for ch in (' ', '"', "\\", "\t")):
        return '"' + folder.replace("\\", "\\\\").replace('"', r"\"") + '"'
    return folder

# Mapping of user-facing sort keys to actual DB column names (allowlist to prevent injection)
VALID_EMAIL_SORT_COLUMNS = {
    "date": "date",
    "source": "source",
    "folder": "folder",
    "sender": "sender",
    "subject": "subject",
    "created_at": "created_at",
}


@router.get("/emails", response_class=HTMLResponse)
def list_emails(
    request: Request,
    _=require_permission(PERMISSIONS["view_emails"]),
    page: int = 1,
    q: str | None = None,
    account: str | None = None,
    folder: str | None = None,
    sender: str | None = None,
    recipient: str | None = None,
    subject: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    filter: str | None = None,
    sort_by: str | None = None,
    sort_order: str | None = None,
):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # Get user_id for timezone formatting
    user_id = request.session.get("user_id")
    if user_id is not None:
        try:
            user_id = int(user_id)
        except (ValueError, TypeError):
            user_id = None

    # Get page_size from user settings, fallback to global settings
    page_size = 50  # Default
    
    if user_id:
        user_result = query("SELECT page_size FROM users WHERE id = :id", {"id": user_id}).mappings().first()
        if user_result and user_result["page_size"]:
            try:
                page_size = int(user_result["page_size"])
            except (TypeError, ValueError):
                page_size = 50
    
    if not user_id or not page_size:
        global_result = query("SELECT value FROM settings WHERE key = 'page_size'").mappings().first()
        if global_result:
            try:
                page_size = int(global_result["value"])
            except (TypeError, ValueError):
                page_size = 50

    page_size = min(max(10, page_size), 500)  # Ensure between 10-500
    page = max(1, page)  # Guard against page <= 0 producing a negative OFFSET
    offset = (page - 1) * page_size

    where = []
    params = {}

    if q:
        where.append(
            "to_tsvector('simple', coalesce(subject,'') || ' ' || coalesce(sender,'') || ' ' || coalesce(recipients,'')) @@ plainto_tsquery(:q)"
        )
        params["q"] = q

    if account:
        where.append("source = :account")
        params["account"] = account

    if folder:
        where.append("folder = :folder")
        params["folder"] = folder

    if sender:
        where.append("sender ILIKE :sender")
        params["sender"] = f"%{sender}%"

    if recipient:
        where.append("recipients ILIKE :recipient")
        params["recipient"] = f"%{recipient}%"

    if subject:
        where.append("subject ILIKE :subject")
        params["subject"] = f"%{subject}%"

    if date_from:
        where.append("COALESCE(date_parsed, created_at) >= :date_from_utc")
        params["date_from_utc"] = user_date_to_utc_range_start(date_from, user_id)

    if date_to:
        where.append("COALESCE(date_parsed, created_at) < :date_to_utc")
        params["date_to_utc"] = user_date_to_utc_range_end(date_to, user_id)

    # Sanitise the filter value to prevent injection via the predefined allow-list
    if filter and filter in VALID_EMAIL_FILTERS:
        if filter == "missing_subject":
            where.append("(subject IS NULL OR subject = '')")
        elif filter == "missing_sender":
            where.append("(sender IS NULL OR sender = '')")
        elif filter == "missing_recipients":
            where.append("(recipients IS NULL OR recipients = '')")
        elif filter == "unscanned":
            where.append("(virus_scanned IS NULL OR virus_scanned = FALSE)")
        elif filter == "virus_detected":
            where.append("virus_detected = TRUE")

    where_sql = "AND " + " AND ".join(where) if where else ""

    # Build ORDER BY from validated allowlist to prevent SQL injection
    sort_col = VALID_EMAIL_SORT_COLUMNS.get(sort_by, "created_at")
    sort_dir = "ASC" if sort_order == "asc" else "DESC"
    # For the date column (stored as TEXT), use the pre-parsed TIMESTAMPTZ column for
    # correct chronological ordering. Fall back to created_at when date_parsed is NULL.
    if sort_col == "date":
        order_sql = f"COALESCE(date_parsed, created_at) {sort_dir}, id {sort_dir}"
    else:
        # Secondary sort ensures stable ordering when primary column has ties
        order_sql = f"{sort_col} {sort_dir}, id {sort_dir}"

    rows = query(
        f"""
        SELECT id, source, folder, uid, subject, sender, recipients, date, created_at,
               virus_scanned, virus_detected, virus_name, scan_timestamp, signature,
               -- Only transfer raw bytes for rows that have a signature to check;
               -- signature-less rows short-circuit to 'no_signature' without them.
               CASE WHEN signature IS NOT NULL THEN raw_email ELSE NULL END AS raw_email,
               CASE WHEN signature IS NOT NULL THEN compressed ELSE NULL END AS compressed
        FROM emails
        WHERE quarantined = FALSE
        {where_sql}
        ORDER BY {order_sql}
        LIMIT :limit OFFSET :offset
        """,
        {**params, "limit": page_size, "offset": offset},
    ).mappings().all()

    total = query(
        f"SELECT COUNT(*) AS c FROM emails WHERE quarantined = FALSE {where_sql}",
        params,
    ).mappings().first()["c"]

    # Archive-wide counters for the page header stat cards. One lightweight
    # aggregate query; wrapped in a try/except so a stats failure can never
    # take down the list page (the cards render zeros instead).
    stats = {"total_emails": 0, "infected_emails": 0, "unscanned_emails": 0, "quarantined_emails": 0}
    try:
        stats_row = query(
            """
            SELECT
                (SELECT COUNT(*) FROM emails WHERE quarantined = FALSE) AS total_emails,
                (SELECT COUNT(*) FROM emails WHERE quarantined = FALSE AND virus_detected = TRUE) AS infected_emails,
                (SELECT COUNT(*) FROM emails WHERE quarantined = FALSE
                    AND (virus_scanned IS NULL OR virus_scanned = FALSE)) AS unscanned_emails,
                (SELECT COUNT(*) FROM quarantined_emails) AS quarantined_emails
            """
        ).mappings().first()
        if stats_row:
            stats = {key: int(stats_row.get(key) or 0) for key in stats}
    except Exception as exc:
        log("error", "Emails", f"Failed to load archive stats: {exc}", "")

    msg = request.session.pop("flash", None)

    # Compute integrity per-row (may be expensive because it needs raw bytes)
    processed = []
    for r in rows:
        rr = dict(r)
        integrity = "unknown"
        integrity_reason = None
        try:
            stored_sig = rr.get("signature")
            raw_blob = rr.get("raw_email")
            compressed_flag = rr.get("compressed")
            if stored_sig is None:
                # Nothing to compare against - skip decompressing and hashing
                # the raw bytes entirely (the row's raw was not even fetched).
                integrity = "no_signature"
                integrity_reason = "No signature was stored when this email was archived"
            elif raw_blob is not None:
                raw = decompress(raw_blob, compressed_flag)
                try:
                    current_sig = compute_signature(raw)
                except Exception:
                    current_sig = None

                if current_sig is None:
                    integrity = "unknown"
                    integrity_reason = "Could not compute current signature"
                elif stored_sig == current_sig:
                    integrity = "ok"
                    integrity_reason = "The current hash matches the original signature"
                else:
                    integrity = "modified"
                    integrity_reason = "Stored hash does not match current hash"
            else:
                integrity = "no_raw"
                integrity_reason = "No raw email data available"
        except Exception as e:
            integrity = "unknown"
            integrity_reason = f"Could not read attachment file from storage: {str(e)}"

        # Format email and scan timestamps according to user preferences.
        rr["date_formatted"] = format_email_date(rr["date"], rr.get("created_at"), user_id)
        rr["scan_timestamp_formatted"] = (
            format_datetime(rr["scan_timestamp"], user_id)
            if rr.get("scan_timestamp") else None
        )

        # remove large raw fields before sending to template
        rr.pop("raw_email", None)
        rr.pop("compressed", None)
        rr["integrity"] = integrity
        rr["integrity_reason"] = integrity_reason
        processed.append(rr)

    return templates.TemplateResponse(
        "emails.html",
        {
            "request": request,
            "emails": processed,
            "page": page,
            "page_size": page_size,
            "total": total,
            "q": q or "",
            "account": account or "",
            "folder": folder or "",
            "sender": sender or "",
            "recipient": recipient or "",
            "subject": subject or "",
            "date_from": date_from or "",
            "date_to": date_to or "",
            "filter": filter or "",
            "sort_by": sort_by or "",
            "sort_order": sort_order or "",
            "stats": stats,
            "flash": msg,
        },
    )


@router.get("/emails/import-export", response_class=HTMLResponse)
def emails_transfer_page(request: Request):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # The page hosts both the import and export forms; a user holding either
    # permission may open it, with each card gated individually (the sidebar
    # link is already shown for either permission).
    checker = PermissionChecker(request)
    can_import = checker.has_permission("import_emails")
    can_export = checker.has_permission("export_emails")
    if not can_import and not can_export:
        return HTMLResponse("Access denied: Insufficient permissions to import or export emails", status_code=403)

    msg = request.session.pop("flash", None)
    # Provide list of fetch accounts so user can assign imported messages to a source
    try:
        accounts_rows = query("SELECT name FROM fetch_accounts ORDER BY name").mappings().all()
        accounts = [r["name"] for r in accounts_rows]
    except Exception:
        accounts = []

    return templates.TemplateResponse(
        "emails-import-export.html",
        {
            "request": request,
            "flash": msg,
            "accounts": accounts,
            "can_import": can_import,
            "can_export": can_export,
        },
    )


def _insert_raw_email(raw: bytes, request: Request, source: str = "import", folder: str = "INBOX") -> str:
    """Insert one raw email into the archive.

    Returns a status string:
      - "ok"       - stored successfully
      - "rejected" - blocked by the import virus policy (infected email)
      - "error"    - could not be stored
    """
    try:
        # Parse headers for metadata
        parsed = parse_email(raw)

        compressed_raw = gzip.compress(raw)
        try:
            sig = compute_signature(raw)
        except Exception:
            sig = None

        # Virus scanning. virus_scanned is only true when a scan actually ran
        # against clamd (see ClamAVScanner.scan) so an unavailable daemon never
        # marks an imported email as "scanned".
        virus_scanned = False
        virus_detected = False
        virus_name = None
        scan_timestamp = None

        scanner = _get_import_scanner()
        if scanner.requires_scan():
            virus_detected, virus_name, scan_timestamp, virus_scanned = scanner.scan(raw)
            if not virus_scanned:
                log(
                    "error",
                    "Import",
                    "ClamAV scan did not complete; imported email was not stored",
                    f"Source: {source}, Folder: {folder}",
                )
                return "error"

            if virus_detected:
                username = request.session.get("username", "unknown")
                log("warning", "Import", f"Virus detected in imported email: {virus_name}", f"User: {username}, Source: {source}, Folder: {folder}")

                create_alert(
                    'error',
                    'Virus Detected in Imported Email',
                    f'Malicious email detected during import: {virus_name}',
                    f"""Virus: {virus_name}
Subject: {parsed["headers"].get("subject", "N/A")}
From: {parsed["headers"].get("from", "Unknown")}
Imported by: {username}
Source: {source}
Folder: {folder}""",
                    trigger_key='virus_detected',
                )

                # Reject infected emails during import
                return "rejected"

        # Use negative UIDs for imported emails so they can never collide with
        # fetched emails (which use positive UIDs). The uid is derived from
        # MIN(uid)-1, which is racy under concurrent imports - retry a few times
        # (recomputing the uid) if another import claimed the same slot.
        def _next_import_uid() -> int:
            row = query(
                "SELECT COALESCE(MIN(uid) - 1, -1) AS next_uid FROM emails WHERE source = :source AND folder = :folder AND uid < 0",
                {"source": source, "folder": folder},
            ).mappings().first()
            return int(row["next_uid"]) if row else -1

        insert_sql = """
            INSERT INTO emails (source, folder, uid, subject, sender, recipients, date, raw_email, signature, compressed, virus_scanned, virus_detected, virus_name, scan_timestamp)
            VALUES (:source, :folder, :uid, :subject, :sender, :recipients, :date, :raw_email, :signature, :compressed, :virus_scanned, :virus_detected, :virus_name, :scan_timestamp)
        """
        inserted = False
        for _attempt in range(3):
            uid = _next_import_uid()
            try:
                execute(
                    insert_sql,
                    {
                        "source": source,
                        "folder": folder,
                        "uid": uid,
                        "subject": parsed["headers"].get("subject", ""),
                        "sender": parsed["headers"].get("from", ""),
                        "recipients": parsed["headers"].get("to", ""),
                        "date": parsed["headers"].get("date", ""),
                        "raw_email": compressed_raw,
                        "signature": sig,
                        "compressed": True,
                        "virus_scanned": virus_scanned,
                        "virus_detected": virus_detected,
                        "virus_name": virus_name,
                        "scan_timestamp": scan_timestamp,
                    },
                )
                inserted = True
                break
            except IntegrityError:
                # Another import claimed this uid concurrently; recompute and retry
                continue

        if not inserted:
            log("error", "Import", f"Failed to insert imported email (uid contention) for source={source}, folder={folder}", "")
            return "error"

        username = request.session.get("username", "unknown")
        log("info", "Import", f"User '{username}' imported an email (source={source}, folder={folder}, uid={uid})", "")
        return "ok"
    except Exception as e:
        log("error", "Import", f"Failed to insert imported email: {str(e)}", "")
        return "error"


@router.post("/emails/import-export")
async def import_emails(request: Request, source: str = Form("import"), folder: str = Form("INBOX"), files: List[UploadFile] = File(...)):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    checker = PermissionChecker(request)
    if not checker.has_permission("import_emails"):
        return HTMLResponse("Access denied: Insufficient permissions to import emails", status_code=403)

    imported = 0
    rejected = 0
    errors = []

    for upload in files:
        filename = upload.filename or ""
        lower = filename.lower()
        # Read at most MAX_IMPORT_FILE_SIZE + 1 bytes so an oversized upload is
        # rejected instead of being read fully into memory.
        content = await upload.read(MAX_IMPORT_FILE_SIZE + 1)
        if len(content) > MAX_IMPORT_FILE_SIZE:
            errors.append(f"{filename}: file too large (max {MAX_IMPORT_FILE_SIZE // (1024 * 1024)} MB)")
            continue

        try:
            if lower.endswith(".eml") or upload.content_type == "message/rfc822":
                status = _insert_raw_email(content, request, source=source, folder=folder)
                _imp, _rej, _err = _record_insert_result(status, filename)
                imported += _imp
                rejected += _rej
                if _err:
                    errors.append(_err)

            elif lower.endswith(".mbox") or lower.endswith(".mbx") or upload.content_type == "application/mbox":
                try:
                    for raw in _iter_mbox_messages(content):
                        status = _insert_raw_email(raw, request, source=source, folder=folder)
                        _imp, _rej, _err = _record_insert_result(status, filename)
                        imported += _imp
                        rejected += _rej
                        if _err:
                            errors.append(_err)
                except Exception as e:
                    errors.append(f"{filename}: failed to parse mbox ({str(e)})")

            elif lower.endswith(".zip") or upload.content_type == "application/zip":
                try:
                    with zipfile.ZipFile(io.BytesIO(content)) as zf:
                        for info in zf.infolist():
                            if info.is_dir():
                                continue
                            inner_name = info.filename
                            inner_lower = inner_name.lower()
                            # Zip-bomb guard: the archive metadata declares the
                            # uncompressed size before any data is read, so an
                            # oversized entry is rejected up front instead of
                            # being inflated fully into memory.
                            if info.file_size > MAX_IMPORT_FILE_SIZE:
                                errors.append(
                                    f"{filename}:{inner_name}: entry too large "
                                    f"(max {MAX_IMPORT_FILE_SIZE // (1024 * 1024)} MB)"
                                )
                                continue
                            try:
                                # Read the entry through a capped stream: a
                                # crafted archive can lie about its size in the
                                # central directory, so also bound the actual
                                # decompression rather than trusting metadata
                                # alone (a valid oversized entry is still caught
                                # by the check below).
                                with zf.open(info) as member:
                                    fcontent = member.read(MAX_IMPORT_FILE_SIZE + 1)
                                if len(fcontent) > MAX_IMPORT_FILE_SIZE:
                                    errors.append(
                                        f"{filename}:{inner_name}: entry too large "
                                        f"(max {MAX_IMPORT_FILE_SIZE // (1024 * 1024)} MB)"
                                    )
                                    continue
                                if inner_lower.endswith(".eml"):
                                    status = _insert_raw_email(fcontent, request, source=source, folder=folder)
                                    _imp, _rej, _err = _record_insert_result(status, f"{filename}:{inner_name}")
                                    imported += _imp
                                    rejected += _rej
                                    if _err:
                                        errors.append(_err)
                                elif inner_lower.endswith(".mbox") or inner_lower.endswith(".mbx"):
                                    for raw in _iter_mbox_messages(fcontent):
                                        status = _insert_raw_email(raw, request, source=source, folder=folder)
                                        _imp, _rej, _err = _record_insert_result(status, f"{filename}:{inner_name}")
                                        imported += _imp
                                        rejected += _rej
                                        if _err:
                                            errors.append(_err)
                                elif inner_lower.endswith(".msg"):
                                    # Outlook .msg is an OLE container, not RFC822
                                    errors.append(f"{filename}:{inner_name}: .msg files are not supported")
                                elif inner_lower.endswith(".pst"):
                                    # PST support removed - do not attempt to parse
                                    errors.append(f"{filename}:{inner_name}: PST files are not supported")
                                else:
                                    # Non-email entries inside the archive are ignored
                                    continue
                            except Exception as e:
                                errors.append(f"{filename}:{inner_name}: {str(e)}")
                except Exception as e:
                    errors.append(f"{filename}: zip extraction failed ({str(e)})")

            elif lower.endswith(".msg"):
                # Outlook .msg is an OLE container, not RFC822 - unsupported
                errors.append(f"{filename}: .msg files are not supported")

            elif lower.endswith(".pst"):
                # PST support removed - do not attempt to parse PST files
                errors.append(f"{filename}: PST files are not supported")

            else:
                errors.append(f"{filename}: unsupported file type")
        except Exception as e:
            errors.append(f"{filename}: {str(e)}")

    flash(request, *_build_import_flash(imported, rejected, errors))

    return RedirectResponse("/emails", status_code=303)


@router.get("/emails/{email_id}", response_class=HTMLResponse)
def view_email(request: Request, email_id: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # The list page requires view_emails; the detail view exposes full email
    # content (body, headers, raw preview) so it must enforce the same check.
    checker = PermissionChecker(request)
    if not checker.has_permission("view_emails"):
        return HTMLResponse("Access denied: Insufficient permissions to view emails", status_code=403)

    row = query(
        """
        SELECT id, source, folder, uid, subject, sender, recipients, date, message_id,
               raw_email, compressed, signature, created_at, virus_scanned, virus_detected, virus_name, scan_timestamp, quarantined
        FROM emails
        WHERE id = :id
        """,
        {"id": email_id},
    ).mappings().first()

    if not row:
        return HTMLResponse("Email not found", status_code=404)

    # Get user_id for timezone formatting
    user_id = request.session.get("user_id")
    if user_id is not None:
        try:
            user_id = int(user_id)
        except (ValueError, TypeError):
            user_id = None

    # Format timestamps according to user preferences
    row = dict(row)  # Convert to dict to make it mutable
    
    # Format scan_timestamp
    if row["scan_timestamp"]:
        row["scan_timestamp_formatted"] = format_datetime(row["scan_timestamp"], user_id)
    else:
        row["scan_timestamp_formatted"] = None
    
    # Format email date if it's a datetime object
    row["date_formatted"] = format_email_date(row["date"], row.get("created_at"), user_id)

    # Decompress defensively: legacy rows can carry NULL raw_email (an old
    # compression-failure bug) or corrupt gzip data; neither may 500 the page.
    raw = None
    if row["raw_email"] is not None:
        try:
            raw = decompress(row["raw_email"], row["compressed"])
            # Ensure bytes type for parsing and preview
            if isinstance(raw, memoryview):
                raw = raw.tobytes()
        except Exception as e:
            log("error", "Emails", f"Failed to decompress email ID {email_id}: {str(e)}", "")

    empty_body = {"text": "", "html": "", "embedded_images": {}, "attachments": []}
    if raw is None:
        preview = ""
        integrity = "no_raw"
        integrity_reason = "No raw email data available"
        parsed = {"headers": {}, "body": empty_body}
        current_sig = None
    else:
        preview = raw[:10000].decode(errors='replace') if isinstance(raw, (bytes, bytearray)) else str(raw)
        try:
            parsed = parse_email(raw)
        except Exception as e:
            log("error", "Emails", f"Failed to parse email ID {email_id}: {str(e)}", "")
            parsed = {"headers": {}, "body": empty_body}

        # compute integrity status
        integrity_reason = None
        try:
            stored_sig = row.get("signature")
            current_sig = compute_signature(raw)
            if stored_sig is None:
                integrity = "no_signature"
                integrity_reason = "No signature was stored when this email was archived"
            elif stored_sig == current_sig:
                integrity = "ok"
                integrity_reason = "The current hash matches the original signature"
            else:
                integrity = "modified"
                integrity_reason = "Stored hash does not match current hash"
        except Exception as e:
            integrity = "unknown"
            integrity_reason = f"Could not read attachment file from storage: {str(e)}"
            current_sig = None

    username = request.session.get("username", "unknown")
    log("info", "Emails", f"User '{username}' viewed email ID {email_id}", "")

    msg = request.session.pop("flash", None)

    return templates.TemplateResponse(
        "email-view.html",
        {
            "request": request,
            "email": row,
            "headers": parsed["headers"],
            "body": parsed["body"],
            "preview": preview,
            "flash": msg,
            "integrity": integrity,
            "integrity_reason": integrity_reason,
            "stored_signature": row.get("signature"),
            "current_signature": current_sig,
        },
    )


@router.get("/emails/{email_id}/download")
def download_email(request: Request, email_id: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # Check if user has permission to export emails
    checker = PermissionChecker(request)
    if not checker.has_permission("export_emails"):
        return HTMLResponse("Access denied: Insufficient permissions to download email files", status_code=403)

    row = query(
        """
        SELECT raw_email, compressed
        FROM emails
        WHERE id = :id
        """,
        {"id": email_id},
    ).mappings().first()

    if not row:
        return HTMLResponse("Not found", status_code=404)

    if row["raw_email"] is None:
        return HTMLResponse("Raw email data not available for this record", status_code=404)

    raw = decompress(row["raw_email"], row["compressed"])

    username = request.session.get("username", "unknown")
    log("info", "Emails", f"User '{username}' downloaded email ID {email_id}", "")

    return StreamingResponse(
        iter([raw]),
        media_type="message/rfc822",
        headers={"Content-Disposition": f'attachment; filename="email-{email_id}.eml"'},
    )


@router.get("/emails/{email_id}/attachments/{index}")
def download_attachment(request: Request, email_id: int, index: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    checker = PermissionChecker(request)
    if not checker.has_permission("export_emails"):
        return HTMLResponse("Access denied: Insufficient permissions to download attachments", status_code=403)

    row = query(
        "SELECT raw_email, compressed, virus_detected, virus_name FROM emails WHERE id = :id",
        {"id": email_id},
    ).mappings().first()

    if not row:
        return HTMLResponse("Not found", status_code=404)

    if row["virus_detected"]:
        virus_name = row["virus_name"] or "Unknown"
        username = request.session.get("username", "unknown")
        log("warning", "Emails", f"User '{username}' attempted to download attachment from infected email ID {email_id} ({virus_name})", "")
        return HTMLResponse(
            f"Download blocked: this email contains a virus ({virus_name}). Remove the virus flag before downloading.",
            status_code=403,
        )

    if row["raw_email"] is None:
        return HTMLResponse("Raw email data not available for this record", status_code=404)

    raw = decompress(row["raw_email"], row["compressed"])
    if isinstance(raw, memoryview):
        raw = raw.tobytes()

    attachment_parts = get_attachment_parts(raw)

    if index < 0 or index >= len(attachment_parts):
        return HTMLResponse("Attachment not found", status_code=404)

    part = attachment_parts[index]
    data = part.get_payload(decode=True) or b""
    raw_filename = part.get_filename() or f"attachment-{index}"
    # Sanitise: strip directory components and replace any remaining path separators
    filename = os.path.basename(raw_filename).replace("/", "_").replace("\\", "_") or f"attachment-{index}"
    content_type = part.get_content_type() or "application/octet-stream"
    encoded_filename = urllib.parse.quote(filename)

    username = request.session.get("username", "unknown")
    log("info", "Emails", f"User '{username}' downloaded attachment {index} from email ID {email_id}", "")

    return StreamingResponse(
        iter([data]),
        media_type=content_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
    )


@router.get("/emails/{email_id}/verify")
def verify_email(request: Request, email_id: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # Check permission to view emails
    checker = PermissionChecker(request)
    if not checker.has_permission("view_emails"):
        return HTMLResponse("Access denied: Insufficient permissions to verify emails", status_code=403)

    row = query(
        "SELECT raw_email, compressed, signature FROM emails WHERE id = :id",
        {"id": email_id},
    ).mappings().first()

    if not row:
        return HTMLResponse("Not found", status_code=404)

    current_sig = None
    if row["raw_email"] is not None:
        raw = decompress(row["raw_email"], row["compressed"])
        try:
            current_sig = compute_signature(raw)
        except Exception:
            current_sig = None

    stored_sig = row.get("signature")

    match = (stored_sig is not None and current_sig is not None and stored_sig == current_sig)

    # Return JSON result
    from fastapi.responses import JSONResponse
    return JSONResponse({"id": email_id, "match": match, "stored_signature": stored_sig, "current_signature": current_sig})


@router.post("/emails/scan")
def scan_emails(
    request: Request,
    ids: List[int] = Form(...),
):
    """Manually scan one or more archived emails with ClamAV."""
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    checker = PermissionChecker(request)
    if not checker.has_permission("scan_emails"):
        return HTMLResponse("Access denied: Insufficient permissions to scan emails", status_code=403)

    if not isinstance(ids, list):
        ids = [ids]
    username = request.session.get("username", "unknown")
    scan_result = _scan_email_ids(ids, username)

    parts = [f"Scanned {scan_result['scanned']} email(s)."]
    if scan_result["clean"]:
        parts.append(f"{scan_result['clean']} clean.")
    if scan_result["infected"]:
        parts.append(f"{scan_result['infected']} infected - review and quarantine as needed.")
    if scan_result["skipped"]:
        parts.append(f"{scan_result['skipped']} skipped.")
    if scan_result["errors"]:
        parts.append("Issues: " + " ".join(scan_result["errors"][:5]))

    category = "error" if scan_result["errors"] and not scan_result["scanned"] else (
        "warning" if scan_result["infected"] or scan_result["errors"] else "success"
    )
    flash(request, " ".join(parts), category)
    log("info", "ClamAV", f"User '{username}' manually scanned {len(ids)} email(s)", "")
    return RedirectResponse("/emails", status_code=303)


@router.post("/emails/{email_id}/quarantine")
def quarantine_single_email(request: Request, email_id: int):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    # Check if user has permission to manage quarantine
    checker = PermissionChecker(request)
    if not checker.has_permission("manage_quarantine"):
        return HTMLResponse("Access denied: Insufficient permissions to quarantine emails", status_code=403)
    
    quarantined = _quarantine_emails([email_id], request.session.get("username", "unknown"))
    
    username = request.session.get("username", "unknown")
    log("warning", "Emails", f"User '{username}' quarantined 1 email (ID: {email_id})", "")
    
    # Create alert for security monitoring
    try:
        create_alert(
            'warning',
            'Email Quarantined',
            f'User {username} quarantined an email',
            f'Email ID: {email_id}',
            trigger_key='email_quarantined',
        )
    except Exception as e:
        log("error", "Emails", f"Failed to create quarantine alert: {str(e)}", "")
    
    if quarantined > 0:
        flash(request, "Email quarantined successfully.", 'success')
    else:
        flash(request, "Email could not be quarantined (may already be quarantined).", 'error')
    
    return RedirectResponse("/emails", status_code=303)


@router.post("/emails/delete")
def perform_delete(
    request: Request,
    ids: List[int] = Form(...),
    mode: str = Form(...),  # "db" or "imap"
):
    """
    Perform the actual delete, either:
    - Database Only
    - Database and Mail Server (IMAP/Gmail/O365)
    """
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    checker = PermissionChecker(request)
    if not checker.has_permission("delete_emails"):
        return HTMLResponse("Access denied: Insufficient permissions to delete emails", status_code=403)

    if not isinstance(ids, list):
        ids = [ids]

    if mode == "db":
        deleted = _delete_emails_from_db(ids)
        username = request.session.get("username", "unknown")
        log("warning", "Emails", f"User '{username}' deleted {deleted} email(s) from database (IDs: {ids})", "")
        flash(request, f"Deleted {deleted} email(s) from the database.", 'success')
        return RedirectResponse("/emails", status_code=303)

    elif mode == "imap":
        deleted, errors = _delete_emails_from_mail_server_and_db(ids)

        username = request.session.get("username", "unknown")
        if errors:
            error_text = " | ".join(errors)
            log("warning", "Emails", f"User '{username}' deleted {deleted} email(s) from IMAP and database with errors (IDs: {ids})", error_text)
            flash(
                request,
                f"Deleted {deleted} email(s) from the database. Mail server deletion had issues: {error_text}",
                'error'
            )
        else:
            log("warning", "Emails", f"User '{username}' deleted {deleted} email(s) from IMAP and database (IDs: {ids})", "")
            flash(
                request,
                f"Deleted {deleted} email(s) from database and mail server.",
                'success'
            )

        return RedirectResponse("/emails", status_code=303)

    else:
        flash(request, "Invalid delete mode selected.", 'error')
        return RedirectResponse("/emails", status_code=303)


@router.post("/emails/quarantine")
def perform_quarantine(
    request: Request,
    ids: List[int] = Form(...),
):
    """
    Quarantine selected emails.
    """
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    checker = PermissionChecker(request)
    if not checker.has_permission("manage_quarantine"):
        return HTMLResponse("Access denied: Insufficient permissions to quarantine emails", status_code=403)

    if not isinstance(ids, list):
        ids = [ids]

    quarantined = _quarantine_emails(ids, request.session.get("username", "unknown"))
    
    username = request.session.get("username", "unknown")
    log("warning", "Emails", f"User '{username}' quarantined {quarantined} email(s) (IDs: {ids})", "")
    
    # Create alert for security monitoring
    try:
        create_alert(
            'warning',
            'Emails Quarantined',
            f'User {username} quarantined {quarantined} email(s)',
            f'Email IDs: {ids}',
            trigger_key='email_quarantined',
        )
    except Exception as e:
        log("error", "Emails", f"Failed to create quarantine alert: {str(e)}", "")
    
    if quarantined > 0:
        flash(request, f"Quarantined {quarantined} email(s).", 'success')
    else:
        flash(request, "No emails were quarantined.", 'info')
    
    return RedirectResponse("/emails", status_code=303)


def _quarantine_emails(ids: List[int], quarantined_by: str) -> int:
    """Quarantine emails by moving them to quarantined_emails table. Returns number quarantined."""
    if not ids:
        return 0

    quarantined_count = 0

    for email_id in ids:
        try:
            # Get email data (carry date_parsed and scan metadata so restored
            # emails keep their original date sorting and honest scan state)
            email = query("""
                SELECT id, source, folder, uid, subject, sender, recipients, date, date_parsed,
                       message_id, raw_email, signature, compressed, virus_name,
                       virus_scanned, virus_detected, scan_timestamp
                FROM emails
                WHERE id = :id AND quarantined = FALSE
            """, {"id": email_id}).mappings().first()

            if not email:
                continue  # Already quarantined or doesn't exist

            # Insert into quarantined_emails and remove from emails atomically so
            # a crash mid-move cannot leave the email in both (or neither) table.
            with transaction() as conn:
                insert_result = conn.execute(text("""
                    INSERT INTO quarantined_emails
                    (original_source, original_folder, original_uid, subject, sender, recipients,
                     date, date_parsed, message_id, raw_email, signature, compressed, virus_name,
                     virus_scanned, virus_detected, scan_timestamp, reason, quarantined_by,
                     original_created_at)
                    VALUES (:source, :folder, :uid, :subject, :sender, :recipients,
                     :date, :date_parsed, :message_id, :raw_email, :signature, :compressed, :virus_name,
                     :vscanned, :vdetected, :scan_ts, :reason, :quarantined_by,
                     :original_created_at)
                    ON CONFLICT DO NOTHING
                """), {
                    "source": email["source"],
                    "folder": email["folder"],
                    "uid": email["uid"],
                    "subject": email["subject"],
                    "sender": email["sender"],
                    "recipients": email["recipients"],
                    "date": email["date"],
                    "date_parsed": email.get("date_parsed"),
                    "message_id": email.get("message_id"),
                    "raw_email": email["raw_email"],
                    "signature": email.get("signature"),
                    "compressed": email["compressed"],
                    "virus_name": email.get("virus_name"),
                    "vscanned": email.get("virus_scanned"),
                    "vdetected": email.get("virus_detected"),
                    "scan_ts": email.get("scan_timestamp"),
                    "reason": "Manually Quarantined",
                    "quarantined_by": quarantined_by,
                    "original_created_at": email.get("created_at")
                })
                # If the same source/folder/UID is already quarantined, retain
                # the existing quarantine record and leave this archive row
                # untouched rather than deleting a clean re-fetched copy.
                if insert_result.rowcount == 0:
                    continue
                conn.execute(text("DELETE FROM emails WHERE id = :id"), {"id": email_id})

            quarantined_count += 1

        except Exception as e:
            log("error", "Emails", f"Failed to quarantine email ID {email_id}: {str(e)}", "")

    return quarantined_count


def _delete_emails_from_db(ids: List[int]) -> int:
    """Delete emails from the database only. Returns number of emails deleted."""
    if not ids:
        return 0
    
    # Delete all at once for better performance
    placeholders = ",".join(f":id{i}" for i in range(len(ids)))
    params = {f"id{i}": email_id for i, email_id in enumerate(ids)}
    
    result = query(
        f"DELETE FROM emails WHERE id IN ({placeholders}) RETURNING id",
        params,
    )
    deleted = len(result.all())
    
    # Track deletion statistics
    if deleted > 0:
        execute(
            """
            INSERT INTO deletion_stats (deletion_date, deletion_type, count, deleted_from_mail_server)
            VALUES (CURRENT_DATE, 'manual', :count, FALSE)
            ON CONFLICT (deletion_date, deletion_type, deleted_from_mail_server)
            DO UPDATE SET count = deletion_stats.count + EXCLUDED.count
            """,
            {"count": deleted},
        )
    
    return deleted


def _delete_emails_from_mail_server_and_db(ids: List[int]) -> tuple[int, list[str]]:
    """
    Delete emails from mail server (IMAP/Gmail/O365) and then from DB.
    Returns (deleted_count, errors).
    """
    errors: list[str] = []
    deleted = 0
    server_deleted = 0
    db_only_deleted = 0

    for mid in ids:
        email_row = query(
            """
            SELECT id, source, folder, uid
            FROM emails
            WHERE id = :id
            """,
            {"id": mid},
        ).mappings().first()

        if not email_row:
            errors.append(f"Email {mid} not found")
            continue

        account = query(
            """
            SELECT name, host, port, username, password_encrypted,
                   use_ssl, require_starttls, account_type
            FROM fetch_accounts
            WHERE name = :name
            """,
            {"name": email_row["source"]},
        ).mappings().first()

        if not account:
            # The fetch account no longer exists, so the mail server copy cannot
            # be addressed; remove the database record rather than failing.
            query("DELETE FROM emails WHERE id = :id", {"id": mid})
            deleted += 1
            db_only_deleted += 1
            errors.append(
                f"Email {mid}: fetch account '{email_row['source']}' no longer exists; "
                f"deleted from database only"
            )
            continue

        # Gmail/O365 accounts are API-based (no IMAP mailbox to delete from);
        # fall back to removing the database record only rather than failing.
        account_type = account.get("account_type", "imap")
        if account_type != "imap":
            query("DELETE FROM emails WHERE id = :id", {"id": mid})
            deleted += 1
            db_only_deleted += 1
            errors.append(
                f"Email {mid}: mail server deletion not supported for "
                f"{account_type} accounts; deleted from database only"
            )
            continue

        # Imported emails use negative synthetic UIDs and have no mail server
        # copy - treat them like the account_type fallback above instead of
        # attempting an IMAP UID STORE with a negative UID (which errors and
        # would leave the email undeletable forever).
        if email_row["uid"] is None or email_row["uid"] <= 0:
            query("DELETE FROM emails WHERE id = :id", {"id": mid})
            deleted += 1
            db_only_deleted += 1
            errors.append(
                f"Email {mid}: no mail server copy (imported email); "
                f"deleted from database only"
            )
            continue

        conn = None
        try:
            # Connect to IMAP using same style as /fetch_accounts/test
            if account["use_ssl"]:
                conn = IMAP4_SSL(account["host"], account["port"])
            else:
                conn = IMAP4(account["host"], account["port"])
                if account["require_starttls"]:
                    conn.starttls()

            password = decrypt_password(account["password_encrypted"])
            conn.login(account["username"], password)

            # Select the folder and delete by UID (quote the folder name so
            # mailboxes containing spaces, e.g. "Sent Items", are accepted)
            folder = email_row["folder"]
            conn.select(_quote_imap_folder(folder))

            uid_str = str(email_row["uid"])
            typ, _ = conn.uid("STORE", uid_str, "+FLAGS", r"(\Deleted)")
            if typ != "OK":
                raise RuntimeError(f"Failed to flag email {mid} for deletion on mail server")

            typ, _ = conn.expunge()
            if typ != "OK":
                raise RuntimeError(f"Failed to expunge email {mid} on mail server")

            # Only delete from DB if IMAP delete succeeded
            query(
                "DELETE FROM emails WHERE id = :id",
                {"id": mid},
            )
            deleted += 1
            server_deleted += 1

        except Exception as e:
            errors.append(f"Email {mid}: {str(e)}")
        finally:
            # Ensure connection is closed even if errors occur
            if conn:
                try:
                    conn.logout()
                except Exception:
                    pass

    # Track deletion statistics (server deletions and DB-only fallbacks separately)
    if server_deleted > 0:
        execute(
            """
            INSERT INTO deletion_stats (deletion_date, deletion_type, count, deleted_from_mail_server)
            VALUES (CURRENT_DATE, 'manual', :count, TRUE)
            ON CONFLICT (deletion_date, deletion_type, deleted_from_mail_server)
            DO UPDATE SET count = deletion_stats.count + EXCLUDED.count
            """,
            {"count": server_deleted},
        )
    if db_only_deleted > 0:
        execute(
            """
            INSERT INTO deletion_stats (deletion_date, deletion_type, count, deleted_from_mail_server)
            VALUES (CURRENT_DATE, 'manual', :count, FALSE)
            ON CONFLICT (deletion_date, deletion_type, deleted_from_mail_server)
            DO UPDATE SET count = deletion_stats.count + EXCLUDED.count
            """,
            {"count": db_only_deleted},
        )

    return deleted, errors


    # import routes moved earlier to avoid collision with /emails/{email_id}




@router.post("/emails/export")
def export_emails(request: Request, q: str = Form(None), account: str = Form(None), folder: str = Form(None), format: str = Form("zip")):
    if not require_login(request):
        return RedirectResponse("/login", status_code=303)

    checker = PermissionChecker(request)
    if not checker.has_permission("export_emails"):
        return HTMLResponse("Access denied: Insufficient permissions to export emails", status_code=403)

    # Build WHERE clause from filters
    where = []
    params = {}
    if q:
        where.append("to_tsvector('simple', coalesce(subject,'') || ' ' || coalesce(sender,'') || ' ' || coalesce(recipients,'')) @@ plainto_tsquery(:q)")
        params["q"] = q
    if account:
        where.append("source = :account")
        params["account"] = account
    if folder:
        where.append("folder = :folder")
        params["folder"] = folder

    where_sql = "WHERE " + " AND ".join(where) if where else ""
    # Always exclude quarantined messages in export by default
    if where_sql:
        where_sql = where_sql + " AND quarantined = FALSE"
    else:
        where_sql = "WHERE quarantined = FALSE"

    # Count first so an empty export can redirect with a friendly message
    # instead of producing an empty archive.
    count_row = query(f"SELECT COUNT(*) as c FROM emails {where_sql}", params).mappings().first()
    if not count_row or not count_row["c"]:
        flash(request, "No emails found to export.", 'error')
        return RedirectResponse("/emails", status_code=303)

    username = request.session.get("username", "unknown")

    # Fetch rows in keyset batches so a very large archive is never loaded into
    # memory at once, and write into a spooled temp file that spills to disk
    # beyond 64 MB instead of building the whole export in RAM.
    EXPORT_BATCH = 500

    def iter_rows():
        last_id = 0
        while True:
            batch = query(
                f"SELECT id, raw_email, compressed FROM emails {where_sql} AND id > :last_id ORDER BY id LIMIT :batch",
                {**params, "last_id": last_id, "batch": EXPORT_BATCH},
            ).mappings().all()
            if not batch:
                return
            for r in batch:
                yield r
            last_id = batch[-1]["id"]

    def stream_file(spool):
        try:
            spool.seek(0)
            while True:
                chunk = spool.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                spool.close()
            except Exception:
                pass

    spool = tempfile.SpooledTemporaryFile(max_size=64 * 1024 * 1024)

    if format == "mbox":
        for r in iter_rows():
            raw = decompress(r["raw_email"], r["compressed"]) if r["raw_email"] is not None else b""
            try:
                # Only the Date header is needed for the mbox "From " line;
                # full parse_email would decode every attachment into memory.
                date_hdr = message_from_bytes(raw).get("Date") or "-"
            except Exception:
                date_hdr = "-"

            from_line = f"From - {date_hdr}\n".encode("utf-8", errors="replace")
            spool.write(from_line)
            spool.write(raw)
            if not raw.endswith(b"\n"):
                spool.write(b"\n")
            spool.write(b"\n")

        log("info", "Export", f"User '{username}' exported emails as mbox", "")
        return StreamingResponse(
            stream_file(spool),
            media_type="application/mbox",
            headers={"Content-Disposition": 'attachment; filename="emails-export.mbox"'},
        )

    with zipfile.ZipFile(spool, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for r in iter_rows():
            raw = decompress(r["raw_email"], r["compressed"]) if r["raw_email"] is not None else b""
            zf.writestr(f"email-{r['id']}.eml", raw)

    log("info", "Export", f"User '{username}' exported emails", "")
    return StreamingResponse(
        stream_file(spool),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="emails-export.zip"'},
    )
