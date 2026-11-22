"""each server's own claude api key (#11), stored encrypted.

keys are encrypted with STASH_SECRET_KEY (a fernet key the host sets once).
lose that secret and every stored key has to be entered again with /setkey.
"""

import anthropic
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


async def check(api_key: str) -> str | None:
    """None if the key works, otherwise a plain reason. listing models costs nothing."""
    client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=1, timeout=15)
    try:
        await client.models.list(limit=1)
    except anthropic.AuthenticationError:
        return "that key didn't work. check it was copied in full"
    except anthropic.PermissionDeniedError:
        return "that key doesn't have permission to use the api"
    except anthropic.APIConnectionError:
        return "i couldn't reach anthropic to check the key. try again in a minute"
    except anthropic.APIStatusError as e:
        return f"anthropic returned an error ({e.status_code}). try again in a minute"
    finally:
        await client.close()
    return None


def looks_like_key(text: str) -> bool:
    return text.startswith("sk-ant-") and len(text) > 20 and " " not in text
