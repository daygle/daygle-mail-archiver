import gzip
from email import message_from_bytes
from email.message import EmailMessage

def decompress(raw: bytes, compressed: bool) -> bytes:
    return gzip.decompress(raw) if compressed else raw

def parse_email(raw: bytes):
    msg: EmailMessage = message_from_bytes(raw)

    def extract_body(m: EmailMessage):
        text = ""
        html = ""

        if m.is_multipart():
            for part in m.walk():
                ctype = part.get_content_type()
                disp = str(part.get("Content-Disposition") or "").lower()

                if "attachment" in disp:
                    continue

                if ctype == "text/plain":
                    text += part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8",
                        errors="replace",
                    )
                elif ctype == "text/html":
                    html += part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8",
                        errors="replace",
                    )
        else:
            ctype = m.get_content_type()
            if ctype == "text/plain":
                text = m.get_payload(decode=True).decode(
                    m.get_content_charset() or "utf-8",
                    errors="replace",
                )
            elif ctype == "text/html":
                html = m.get_payload(decode=True).decode(
                    m.get_content_charset() or "utf-8",
                    errors="replace",
                )

        return {"text": text, "html": html}

    headers = {
        "subject": msg.get("Subject", ""),
        "from": msg.get("From", ""),
        "to": msg.get("To", ""),
        "cc": msg.get("Cc", ""),
        "date": msg.get("Date", ""),
        "message_id": msg.get("Message-ID", ""),
    }

    return {
        "headers": headers,
        "body": extract_body(msg),
    }