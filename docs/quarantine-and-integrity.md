# Quarantine, ClamAV, and integrity verification

## ClamAV processing

When enabled and available, the worker scans eligible incoming email bytes with ClamAV before normal archiving. Scanning can be skipped when ClamAV is disabled, unavailable within the configured failure policy, or the message exceeds the configured scan size. Configure the scanner in **Global Settings → Virus Scanning (ClamAV)**.

| Action | Behavior for a detected message |
| --- | --- |
| `quarantine` | Store the message in `quarantined_emails` with scan metadata |
| `reject` | Do not archive the detected message |
| `log_only` | Record the detection and continue without quarantining it |

Relevant settings include the ClamAV host and port, maximum scan size, quarantine retention days, quarantine encryption, and the failure grace period. The grace period prevents brief ClamAV signature-reload outages from immediately causing repeated unavailable/recovered transitions. The worker records operational events and trigger-aware alerts for scanner failures and recovery.

## Viewing and managing quarantine

Users with `view_quarantine` can browse quarantine records. Depending on assigned permissions:

- `restore_quarantine` restores a message to the archive
- `delete_quarantine` permanently deletes the quarantine record
- Server-side deletion is attempted only for supported provider/account situations; API-based accounts are not treated as IMAP connections

Restores and deletes are protected by server-side permission checks. A restore conflict leaves the quarantine record intact rather than overwriting an existing archive message.

## Deduplication and metadata

Quarantines are unique by `(original_source, original_folder, original_uid)` when the UID is non-null. This prevents a fetch-state reset or retry from creating duplicate quarantine rows for the same provider message. Legacy duplicates are cleaned deterministically during schema application.

Quarantine records preserve:

- Original source, folder, UID, and message identifiers
- Raw message bytes and compression state
- SHA-256 signature, when present
- `virus_scanned`, `virus_detected`, `virus_name`, and `scan_timestamp`
- Quarantine reason and actor/source metadata

Restoring a message carries the scan metadata and signature back to the archive. A duplicate quarantine attempt does not replace the first record's metadata.

## Integrity verification

Archived email integrity is based on a SHA-256 signature of the raw RFC822 bytes. The application recomputes the signature and compares it with the stored signature. The UI can report:

- **Valid**: current bytes match the stored signature
- **Invalid/modified**: the bytes differ
- **No signature**: no signature was stored for the message
- **No raw data**: the bytes are unavailable for verification
- **Unknown**: verification could not be completed

Integrity verification detects tampering or unexpected changes; it does not encrypt data. A valid signature also does not mean that the email is safe-ClamAV results and integrity results answer different questions.

## Optional quarantine encryption

To encrypt quarantined raw bytes at rest:

1. Generate a dedicated Fernet key:

   ```bash
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

2. Set `CLAMAV_QUARANTINE_KEY` for the API and worker.
3. Enable `clamav_quarantine_encrypt` from Global Settings or the database settings table.

Do not reuse `IMAP_PASSWORD_KEY`. Protect backups because they contain encrypted data and the configuration keys. Rotating the quarantine key without a migration plan makes previously encrypted records unreadable; retain the old key or re-encrypt records before removing it.
