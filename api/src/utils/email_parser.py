import gzip
from email import message_from_bytes
from email.message import EmailMessage
import hashlib
import base64

def decompress(raw: bytes, compressed: bool) -> bytes:
    return gzip.decompress(raw) if compressed else raw

def parse_email(raw: bytes):
    msg: EmailMessage = message_from_bytes(raw)

    def extract_body(m: EmailMessage):
        text = ""
        html = ""
        embedded_images = {}

        if m.is_multipart():
            for part in m.walk():
                ctype = part.get_content_type()
                disp = str(part.get("Content-Disposition") or "").lower()
                content_id = part.get("Content-ID", "").strip("<>")

                # Handle embedded images
                if content_id and ctype.startswith("image/"):
                    try:
                        image_data = part.get_payload(decode=True)
                        if image_data:
                            # Create data URL for the image
                            encoded = base64.b64encode(image_data).decode('ascii')
                            data_url = f"data:{ctype};base64,{encoded}"
                            embedded_images[content_id] = data_url
                    except Exception:
                        pass
                    continue

                if "attachment" in disp:
                    continue

                if ctype == "text/plain":
                    text += part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8",
                        errors="replace",
                    )
                elif ctype == "text/html":
                    html_part = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8",
                        errors="replace",
                    )
                    html += html_part
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

        # Replace cid: references with data URLs
        if html and embedded_images:
            for cid, data_url in embedded_images.items():
                html = html.replace(f'cid:{cid}', data_url)

        return {"text": text, "html": html, "embedded_images": embedded_images}

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


def compute_signature(raw: bytes) -> str:
    """Compute SHA256 hex signature of the raw email bytes."""
    if raw is None:
        return ""
    h = hashlib.sha256()
    h.update(raw)
    return h.hexdigest()