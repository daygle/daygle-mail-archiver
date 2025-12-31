from imap_client import connect
from storage import store_message, hash_message
from db import engine
from config import Config
from mailparser import parse_from_bytes
from sqlalchemy import text

def process_account():
    with connect() as client:
        client.login(Config.IMAP_USER, Config.IMAP_PASSWORD)
        client.select_folder("INBOX")

        uids = client.search(["ALL"])

        for uid in uids:
            raw = client.fetch(uid, ["RFC822"])[uid][b"RFC822"]
            msg_hash = hash_message(raw)

            with engine.begin() as conn:
                exists = conn.execute(
                    text("SELECT 1 FROM messages WHERE uid=:uid AND hash=:hash"),
                    {"uid": uid, "hash": msg_hash}
                ).fetchone()

                if exists:
                    continue

            parsed = parse_from_bytes(raw)
            path = store_message("default", "INBOX", uid, raw)

            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO messages (account, folder, uid, hash, subject, sender, recipients, date, storage_path)
                    VALUES (:account, :folder, :uid, :hash, :subject, :sender, :recipients, :date, :path)
                """), {
                    "account": "default",
                    "folder": "INBOX",
                    "uid": uid,
                    "hash": msg_hash,
                    "subject": parsed.subject,
                    "sender": parsed.from_[0][1] if parsed.from_ else None,
                    "recipients": ", ".join([r[1] for r in parsed.to]) if parsed.to else None,
                    "date": parsed.date,
                    "path": path
                })

            if Config.DELETE_AFTER_PROCESSING:
                client.delete_messages(uid)
                client.expunge()