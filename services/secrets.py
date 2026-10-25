from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class SecretsNotConfigured(Exception):
    """No encryption key is set, so provider secrets cannot be stored or read."""


class SecretsDecryptionError(Exception):
    """Stored secrets cannot be decrypted with any configured key."""


class SecretBox:
    """
    Encrypts a provider's secret fields at rest (Fernet, AES-128-CBC + HMAC).

    `keys` is the comma-separated PULSE_SECURITY__SECRETS_KEY value: the first
    key encrypts, every key can decrypt, so a key is rotated by prepending
    the new one and re-saving providers at leisure.
    """

    def __init__(self, keys: str = "") -> None:
        parts = [k.strip() for k in keys.split(",") if k.strip()]
        self._fernet = MultiFernet([Fernet(k) for k in parts]) if parts else None

    @property
    def configured(self) -> bool:
        return self._fernet is not None

    def encrypt(self, secrets: dict[str, Any]) -> str | None:
        if not secrets:
            return None
        if self._fernet is None:
            raise SecretsNotConfigured(
                "Set PULSE_SECURITY__SECRETS_KEY (a Fernet key) to store provider secrets"
            )
        return self._fernet.encrypt(json.dumps(secrets).encode()).decode()

    def decrypt(self, token: str | None) -> dict[str, Any]:
        if not token:
            return {}
        if self._fernet is None:
            raise SecretsNotConfigured("PULSE_SECURITY__SECRETS_KEY is not set")
        try:
            return json.loads(self._fernet.decrypt(token.encode()))
        except InvalidToken as exc:
            raise SecretsDecryptionError("Provider secrets cannot be decrypted") from exc


def generate_key() -> str:
    return Fernet.generate_key().decode()
