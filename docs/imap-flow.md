# IMAP Processing Flow

1. SELECT INBOX
2. SEARCH for all UIDs
3. For each UID:
   - FETCH RFC822
   - Hash content
   - Check DB for duplicates
   - Store .eml
   - Insert metadata
   - Delete or move message