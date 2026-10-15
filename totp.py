"""TOTP (RFC 6238) and recovery-code handling, implemented on the standard library."""

import base64
import binascii
import hashlib
import hmac
import secrets
import struct
import time

DIGITS = 6
PERIOD = 30
WINDOW = 1  # accept one step either side to tolerate clock drift
RECOVERY_COUNT = 8


def generate_secret(length: int = 20) -> str:
    """A base32 secret, the format authenticator apps expect."""
    raw = secrets.token_bytes(length)
    return base64.b32encode(raw).decode("ascii").rstrip("=")


def _normalise(secret: str) -> bytes:
    cleaned = (secret or "").strip().replace(" ", "").upper()
    cleaned += "=" * (-len(cleaned) % 8)
    return base64.b32decode(cleaned, casefold=True)


def _hotp(secret: str, counter: int) -> str:
    digest = hmac.new(_normalise(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**DIGITS)).zfill(DIGITS)


def current_code(secret: str, at: float | None = None) -> str:
    counter = int((at if at is not None else time.time()) // PERIOD)
    return _hotp(secret, counter)


def verify(secret: str, token: str, at: float | None = None) -> bool:
    """Constant-time check that tolerates one period of drift either way."""
    token = (token or "").strip().replace(" ", "")
    if not token.isdigit() or len(token) != DIGITS:
        return False
    counter = int((at if at is not None else time.time()) // PERIOD)
    try:
        for step in range(-WINDOW, WINDOW + 1):
            if hmac.compare_digest(_hotp(secret, counter + step), token):
                return True
    except (binascii.Error, ValueError):
        # A malformed secret can never match a well-formed token.
        return False
    return False


def otpauth_uri(secret: str, email: str, issuer: str = "Indian Store") -> str:
    """The otpauth:// link a QR code would encode."""
    from urllib.parse import quote

    label = quote(f"{issuer}:{email}")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={DIGITS}&period={PERIOD}"
    )


def new_recovery_codes() -> list[str]:
    return [secrets.token_hex(4).upper() for _ in range(RECOVERY_COUNT)]


def store_recovery_codes(codes: list[str]) -> str:
    return ",".join(codes)


def parse_recovery_codes(raw: str) -> list[str]:
    return [c.strip().upper() for c in (raw or "").split(",") if c.strip()]


def consume_recovery_code(raw: str, candidate: str) -> tuple[bool, str]:
    """Burn a recovery code on use and return the remaining set."""
    remaining = parse_recovery_codes(raw)
    candidate = (candidate or "").strip().upper()
    if candidate and candidate in remaining:
        remaining.remove(candidate)
        return True, ",".join(remaining)
    return False, raw
