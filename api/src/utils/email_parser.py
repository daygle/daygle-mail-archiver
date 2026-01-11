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
        debug_parts = []

        if m.is_multipart():
            debug_parts = []
            for part in m.walk():
                ctype = part.get_content_type()
                disp = str(part.get("Content-Disposition") or "").lower()
                original_cid = part.get("Content-ID", "")
                debug_parts.append(f"{ctype}, disp={disp}, cid={original_cid}")

                # Handle embedded images
                if ctype.startswith("image/"):
                    # Check if this image has a Content-ID
                    if original_cid:
                        try:
                            image_data = part.get_payload(decode=True)
                            if image_data and len(image_data) < 1024 * 1024:  # Skip images larger than 1MB
                                # Create data URL for the image
                                encoded = base64.b64encode(image_data).decode('ascii')
                                data_url = f"data:{ctype};base64,{encoded}"
                                
                                # Store with cleaned content_id as key
                                cleaned_cid = original_cid.strip("<>")
                                if '@' in cleaned_cid:
                                    cleaned_cid = cleaned_cid.split('@')[0]
                                embedded_images[cleaned_cid] = data_url
                                
                                # Also store with original content_id variations for replacement
                                embedded_images[original_cid] = data_url
                                embedded_images[original_cid.strip("<>")] = data_url
                        except Exception:
                            pass
                        continue
                # Also check for application/octet-stream with Content-ID (potential embedded images)
                elif ctype == "application/octet-stream" and original_cid:
                    try:
                        image_data = part.get_payload(decode=True)
                        if image_data and len(image_data) < 1024 * 1024:
                            # Try to detect if it's actually an image by checking the first few bytes
                            if len(image_data) > 4:
                                # Check for common image signatures
                                if image_data.startswith(b'\xff\xd8\xff'):  # JPEG
                                    img_ctype = "image/jpeg"
                                elif image_data.startswith(b'\x89PNG\r\n\x1a\n'):  # PNG
                                    img_ctype = "image/png"
                                elif image_data.startswith(b'GIF87a') or image_data.startswith(b'GIF89a'):  # GIF
                                    img_ctype = "image/gif"
                                elif image_data.startswith(b'BM'):  # BMP
                                    img_ctype = "image/bmp"
                                else:
                                    img_ctype = None
                                
                                if img_ctype:
                                    encoded = base64.b64encode(image_data).decode('ascii')
                                    data_url = f"data:{img_ctype};base64,{encoded}"
                                    
                                    cleaned_cid = original_cid.strip("<>")
                                    if '@' in cleaned_cid:
                                        cleaned_cid = cleaned_cid.split('@')[0]
                                    embedded_images[cleaned_cid] = data_url
                                    embedded_images[original_cid] = data_url
                                    embedded_images[original_cid.strip("<>")] = data_url
                    except Exception:
                        pass
                    # If no Content-ID, check if it's referenced in HTML (this is less reliable)
                    # For now, we'll skip images without Content-ID

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
            debug_parts = ["Not multipart"]
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
            import re
            for cid_key, data_url in embedded_images.items():
                # Use regex to replace various cid: formats more flexibly
                # Escape special regex characters in cid_key
                escaped_cid = re.escape(cid_key)
                
                # Replace cid: followed by the content-id in various formats
                patterns = [
                    r'cid:\s*' + escaped_cid,  # cid: followed by cid_key (possibly with spaces)
                    r'cid:\s*<[^>]*' + re.escape(cid_key.split('@')[0]) + r'[^>]*>',  # cid:<...localpart...>
                ]
                
                for pattern in patterns:
                    html = re.sub(pattern, data_url, html, flags=re.IGNORECASE)
            
            # Add debug info to HTML
            debug_info = f"<!-- Debug: Found {len(embedded_images)} embedded images: {list(embedded_images.keys())} -->"
            html = debug_info + html

        return {"text": text, "html": html, "embedded_images": embedded_images, "debug_parts": debug_parts}

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