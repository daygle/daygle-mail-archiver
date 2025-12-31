from imapclient import IMAPClient
from config import Config

def connect():
    return IMAPClient(
        Config.IMAP_HOST,
        port=Config.IMAP_PORT,
        ssl=Config.IMAP_USE_SSL
    )
