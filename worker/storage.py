import os
import hashlib

from config import Config

def ensure_dir(path):
    os.makedirs(path, exist_ok=True)

def hash_message(raw_bytes):
    return hashlib.sha256(raw_bytes).hexdigest()

def store_message(account, folder, uid, raw_bytes):
    year = "unknown"
    base = os.path.join(Config.STORAGE_DIR, account, folder)
    ensure_dir(base)

    filename = f"{uid}.eml"
    path = os.path.join(base, filename)

    with open(path, "wb") as f:
        f.write(raw_bytes)

    return path