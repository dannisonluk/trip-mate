"""Field-level encryption for sensitive profile fields (e.g. phone numbers).

Uses Fernet (AES-128-CBC + HMAC) with a key derived from SECRET_KEY, so local
development needs no extra configuration. Ciphertext is what lands in the
database, so a DB dump alone never reveals contact numbers.

NOTE — this module currently has **no call sites**. Phone numbers became a login
identifier rather than a profile field, so there is nothing left to encrypt.
It is retained deliberately as the single sanctioned entry point for encrypting
a future contact field (tracked as tech debt #5).

Two consequences worth knowing before you wire it up:

* The key is derived from `SECRET_KEY`, so **rotating `SECRET_KEY` makes every
  existing ciphertext undecryptable** (`decrypt_value` returns `None`, silently).
  A production deployment that starts using this must introduce a dedicated,
  separately-rotatable key first — there is no `FIELD_ENCRYPTION_KEY` setting
  today, so do not assume one exists.
* `_fernet` is built at import time, so importing this module requires
  `SECRET_KEY` to be set. Nothing imports it yet; keep that in mind if you add
  it to a startup path that runs earlier than settings are loaded.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _derive_key() -> bytes:
    digest = hashlib.sha256(f"tripmate-field-enc::{settings.SECRET_KEY}".encode()).digest()
    return base64.urlsafe_b64encode(digest)


_fernet = Fernet(_derive_key())


def encrypt_value(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str | None) -> str | None:
    if not ciphertext:
        return None
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError):
        return None
