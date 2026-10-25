"""
End-to-end suite: needs Postgres + Redis (`docker compose up -d db redis`).

    PULSE_E2E=1 uv run pytest tests/e2e -v

If your Redis port clashes with another project, start compose with
PULSE_REDIS_PORT=6390 and export PULSE_REDIS__PORT=6390.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("PULSE_E2E"), reason="set PULSE_E2E=1 to run")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("PULSE_E2E"):
        return
    skip = pytest.mark.skip(reason="set PULSE_E2E=1 to run (needs Postgres + Redis)")
    for item in items:
        if "tests/e2e" in str(item.fspath):
            item.add_marker(skip)
