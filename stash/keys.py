"""each server's own ai api key (#11), stored encrypted.

keys are encrypted with STASH_SECRET_KEY (a fernet key the host sets once).
lose that secret and every stored key has to be entered again with /setkey.
"""

from cryptography.fernet import Fernet, InvalidToken


class KeyStoreUnavailable(Exception):
    """the host hasn't set STASH_SECRET_KEY, so keys can't be stored."""


def _fernet(secret: str | None) -> Fernet:
    if not secret:
        raise KeyStoreUnavailable()
    return Fernet(secret.encode())


def encrypt(secret: str | None, api_key: str) -> bytes:
    return _fernet(secret).encrypt(api_key.encode())


def decrypt(secret: str | None, blob: bytes | None) -> str | None:
    """the stored key, or None if there isn't one or it can't be read any more."""
    if not blob or not secret:
        return None
    try:
        return _fernet(secret).decrypt(bytes(blob)).decode()
    except InvalidToken:
        return None  # encrypted with a different STASH_SECRET_KEY
