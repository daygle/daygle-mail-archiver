import time
import logging
import os

from sqlalchemy import text

from config import config, engine
from imap_client import connect

log = logging.getLogger("worker")
logging.basicConfig(level=logging.INFO)


def log_error(source: str, message: str, details: str | None = None):
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO error_log (source, message, details)
                VALUES (:source, :message, :details)
                """
            ),
            {
                "source": source,
                "message": message,
                "details": details or "",
            },
        )


def update_account_status(last_heartbeat=None, last_success=None, last_error=None):
    sets = []
    params = {"id": config.id}

    if last_heartbeat is not None:
        sets.append("last_heartbeat = to_timestamp(:hb)")
        params["hb"] = last_heartbeat
    if last_success is not None:
        sets.append("last_success = to_timestamp(:succ)")
        params["succ"] = last_success
    if last_error is not None:
        sets.append("last_error = :err")
        params["err"] = last_error

    if not sets:
        return

    sql = f"""
        UPDATE imap_accounts
        SET {", ".join(sets)}
        WHERE id = :id
    """

    with engine.begin() as conn:
        conn.execute(text(sql), params)


def process_messages() -> int:
    config.reload()

    client = connect()
    if not config.password:
        raise RuntimeError(f"No password configured for account '{config.name}'")

    client.login(config.username, config.password)
    client.select_folder("INBOX")

    messages = client.search(["ALL"])
    log.info(f"[{config.name}] Found {len(messages)} messages")

    processed = 0
    os.makedirs(config.storage_dir, exist_ok=True)

    for uid in messages:
        msg = client.fetch([uid], ["RFC822"])[uid][b"RFC822"]

        path = os.path.join(config.storage_dir, f"{config.name}-{uid}.eml")
        with open(path, "wb") as f:
            f.write(msg)

        log.info(f"[{config.name}] Saved message UID {uid} to {path}")

        if config.delete_after_processing:
            client.delete_messages([uid])
            client.expunge()
            log.info(f"[{config.name}] Deleted message UID {uid}")

        processed += 1

    client.logout()
    return processed


def main():
    while True:
        start = time.time()
        processed = 0
        error_msg = None

        try:
            processed = process_messages()
            update_account_status(
                last_heartbeat=time.time(),
                last_success=time.time(),
                last_error=None,
            )
        except Exception as e:
            error_msg = str(e)
            log.error(f"[{config.name}] Error in worker: {e}")
            log_error(f"worker:{config.name}", "Worker cycle failed", error_msg)
            update_account_status(
                last_heartbeat=time.time(),
                last_error=error_msg,
            )

        config.reload()
        interval = config.poll_interval_seconds
        duration = int(time.time() - start)

        log.info(
            f"[{config.name}] Cycle complete. Messages processed: {processed}. "
            f"Duration: {duration}s. Sleeping for {interval}s."
        )

        time.sleep(interval)


if __name__ == "__main__":
    main()