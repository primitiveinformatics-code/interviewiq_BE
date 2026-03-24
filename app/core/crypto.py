"""
Shared document encryption helpers.

Key derivation: PBKDF2-HMAC-SHA256 over SECRET_KEY + ENCRYPTION_SALT.
This produces a proper 32-byte key that is then base64url-encoded for Fernet.

IMPORTANT — first-time deployment with an existing database:
  Run  scripts/reencrypt_documents.py  BEFORE deploying this change
  to migrate documents encrypted with the old weak key.
"""
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from app.core.config import settings

_fernet_instance: Fernet | None = None


def _fernet() -> Fernet:
    """Return a cached Fernet instance built from a properly derived key."""
    global _fernet_instance
    if _fernet_instance is None:
        salt = (settings.ENCRYPTION_SALT or settings.SECRET_KEY[:16]).encode()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480_000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(settings.SECRET_KEY.encode()))
        _fernet_instance = Fernet(key)
    return _fernet_instance


def encrypt_content(content: str) -> str:
    return _fernet().encrypt(content.encode()).decode()


def decrypt_content(encrypted: str) -> str:
    return _fernet().decrypt(encrypted.encode()).decode()
