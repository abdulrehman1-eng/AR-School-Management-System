"""
whatsapp_notify.py — Open WhatsApp Web with a pre-filled message.

No auto-send. User must press Send themselves.
Uses only the Python standard library (webbrowser + urllib.parse).
"""

import re
import webbrowser
from urllib.parse import quote


def _normalize_pk_phone(phone: str) -> str:
    """Convert common Pakistan mobile formats to international digits only.

    Examples:
        03211234567  -> 923211234567
        3211234567   -> 923211234567
        +92 321 1234567 -> 923211234567
        923211234567 -> 923211234567
    Returns empty string if the number is unusable.
    """
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone).strip())
    if not digits:
        return ""
    if digits.startswith("92") and len(digits) >= 12:
        return digits
    if digits.startswith("0") and len(digits) >= 11:
        return "92" + digits[1:]
    if len(digits) == 10 and digits.startswith("3"):
        return "92" + digits
    return digits


def open_whatsapp(phone: str, message: str) -> bool:
    """Open WhatsApp Web chat for `phone` with `message` pre-filled.

    Returns True if a browser tab was opened, False if the number was invalid.
    """
    # WhatsApp Integration
    number = _normalize_pk_phone(phone)
    if not number:
        return False
    url = f"https://web.whatsapp.com/send?phone={number}&text={quote(message)}"
    webbrowser.open(url)
    return True


__all__ = ["open_whatsapp", "_normalize_pk_phone"]
