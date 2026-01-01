import time
import logging
import os

from sqlalchemy import text

from config import config, engine
from imap_client import connect

log = logging.getLogger("worker")
logging.basicConfig(level=logging.INFO)


def update_worker_status(
    last_heartbeat: float | None = None,
    last_success: float | None = None,
    last_error: str | None = None,
    run_duration_seconds: int | None = None,
    messages_processed: int | None = None,
):
    with engine.begin() as conn:
        sets = []
        params = {"id": 1}

        if last_heartbeat is not None:
            sets.append("last_heartbeat = to_timestamp(:last_heartbeat)")
            params["last_heartbeat"] = last_heartbeat
        if last_success is not None:
            sets.append("last_success = to_timestamp(:last_success)")
            params["last_success"] = last_success
        if last_error is not None:
            sets.append("last_error = :last_error")
            params["last_error"] = last_error
        if run_duration_seconds is not None:
            sets.append("last_run_duration_seconds = :dur")
            params["dur"] = run_duration_seconds
        if messages_processed is not None:
            sets.append("messages_processed = :msg_count")
            params["msg_count"] = messages_processed

        if not sets:
            return

        sql = f"""
            UPDATE worker_status
            SET {", ".join(sets)}
            WHERE id = :id
        """
        conn.execute(text(sql), params)


def process_messages() -> int:
    config.reload()

    client = connect()
    client.login(config.IMAP_USER, config.IMAP_PASSWORD)

    client.select_folder("INBOX")

    messages = client.search(["ALL"])
    log.info(f"Found {len(messages)} messages")

    processed = 0

    os.makedirs(config.STORAGE_DIR, exist_ok=True)

    for uid in messages:
        msg = client.fetch([uid], ["RFC822"])[uid][b"RFC822"]

        path = os.path.join(config.STORAGE_DIR, f"{uid}.eml")
        with open(path, "wb") as f:
            f.write(msg)

        log.info(f"Saved message UID {uid} to {path}")

        if config.DELETE_AFTER_PROCESSING:
            client.delete_messages([uid])
            client.expunge()
            log.info(f"Deleted message UID {uid}")

        processed += 1

    client.logout()
    return processed


def main():
    while True:
        start = time.time()
        messages_processed = 0
        error_msg = None

        try:
            messages_processed = process_messages()
            update_worker_status(
                last_heartbeat=time.time(),
                last_success=time.time(),
                last_error=None,
                run_duration_seconds=int(time.time() - start),
                messages_processed=messages_processed,
            )
        except Exception as e:
            error_msg = str(e)
            log.error(f"Error in worker: {e}")
            update_worker_status(
                last_heartbeat=time.time(),
                last_error=error_msg,
                run_duration_seconds=int(time.time() - start),
                messages_processed=messages_processed,
            )

        config.reload()
        interval = config.POLL_INTERVAL_SECONDS
        log.info(
            f"Cycle complete. Messages processed: {messages_processed}. "
            f"Sleeping for {interval} seconds."
        )
        time.sleep(interval)


if __name__ == "__main__":
    main()