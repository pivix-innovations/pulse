from __future__ import annotations

from typing import Dict
from config.base import ConfigSection


class SecuritySettings(ConfigSection):
    # Maps an ingress API key to the tenant UUID it authenticates.
    # e.g. PULSE_SECURITY__INGRESS_API_KEYS={"dev-key": "00000000-0000-0000-0000-000000000001"}
    ingress_api_keys: Dict[str, str] = {}

    # Fernet key(s) encrypting provider secrets at rest; comma-separate to
    # rotate (first encrypts, all decrypt). Generate one with:
    #   python -c "from services.secrets import generate_key; print(generate_key())"
    secrets_key: str = ""
