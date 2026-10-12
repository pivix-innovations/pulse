from __future__ import annotations

from typing import Dict
from config.base import ConfigSection


class SecuritySettings(ConfigSection):
    # Maps an ingress API key to the tenant UUID it authenticates.
    # e.g. PULSE_SECURITY__INGRESS_API_KEYS={"dev-key": "00000000-0000-0000-0000-000000000001"}
    ingress_api_keys: Dict[str, str] = {}
