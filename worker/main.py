import time
from config import Config
from db import init_db
from processor import process_account

def main():
    init_db()
    while True:
        process_account()
        time.sleep(Config.POLL_INTERVAL_SECONDS)

if __name__ == "__main__":
    main()