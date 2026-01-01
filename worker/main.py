import time
import logging
from config import config
from imap_client import connect
from imapclient import IMAPClient
import os

log = logging.getLogger("worker")
logging.basicConfig(level=logging.INFO)


def process_messages():
    config.reload()

    client = connect()
    client.login(config.IMAP_USER, config.IMAP_PASSWORD)

    client.select_folder("INBOX")

    messages = client.search(["ALL"])
    log.info(f"Found {len(messages)} messages")

    for uid in messages:
        msg = client.fetch([uid], ["RFC822"])[uid][b"RFC822"]

        # Ensure storage directory exists
        os.makedirs(config.STORAGE_DIR, exist_ok=True)

        path = os.path.join(config.STORAGE_DIR, f"{uid}.eml")
        with open(path, "wb") as f:
            f.write(msg)

        log.info(f"Saved message UID {uid} to {path}")

        if config.DELETE_AFTER_PROCESSING:
            client.delete_messages([uid])
            client.expunge()
            log.info(f"Deleted message UID {uid}")

    client.logout()


def main():
    while True:
        try:
            process_messages()
        except Exception as e:
            log.error(f"Error: {e}")

        config.reload()
        interval = config.POLL_INTERVAL_SECONDS
        log.info(f"Sleeping for {interval} seconds")
        time.sleep(interval)


if __name__ == "__main__":
    main()