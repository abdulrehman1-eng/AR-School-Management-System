"""
security.py — Password hashing & verification.

The original app stored plain-text passwords. That's fixed here using
PBKDF2-HMAC-SHA256 with a random per-user salt (via Python's stdlib
`hashlib`, no extra dependency required). A migration path is included so
any pre-existing plain-text password in an old database is transparently
upgraded to a hash the moment its owner logs in successfully — no manual
password reset is forced on existing users.
"""

import hashlib
import os
import binascii

_ITERATIONS = 200_000


def hash_password(plain_password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${binascii.hexlify(salt).decode()}${binascii.hexlify(dk).decode()}"


def verify_password(plain_password: str, stored: str) -> bool:
    """Returns True if plain_password matches the stored hash (or, for
    legacy un-migrated rows, the stored plain text)."""
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, iterations, salt_hex, hash_hex = stored.split("$")
            salt = binascii.unhexlify(salt_hex)
            expected = binascii.unhexlify(hash_hex)
            dk = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt, int(iterations))
            return dk == expected
        except Exception:
            return False
    # Legacy plain-text row (pre-upgrade database) — compare directly.
    return plain_password == stored
