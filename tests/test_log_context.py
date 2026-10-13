from __future__ import annotations

import json
import logging

from config.logging import LoggingSettings
from telemetry.log_context import bind, configure_logging, update_current


def _capture(settings: LoggingSettings, capsys):
    configure_logging(settings)
    log = logging.getLogger("pulse.test")
    return log


def test_json_lines_carry_bound_ids_and_nested_binds_merge(capsys):
    log = _capture(LoggingSettings(json=True), capsys)
    with bind(tenant_id="t1", attempt_id="a1"):
        update_current(notification_id="n1")
        log.info("sending %s", "mail")
    log.info("outside")
    lines = [json.loads(line) for line in capsys.readouterr().err.splitlines() if "pulse.test" in line]
    assert lines[0]["message"] == "sending mail"
    assert (lines[0]["tenant_id"], lines[0]["notification_id"], lines[0]["attempt_id"]) == ("t1", "n1", "a1")
    assert "tenant_id" not in lines[1]            # cleared once the block exits


def test_text_format_shows_ids_and_dashes_when_unbound(capsys):
    log = _capture(LoggingSettings(json=False), capsys)
    with bind(tenant_id="t9"):
        log.warning("bound")
    log.warning("unbound")
    out = capsys.readouterr().err
    assert "tenant=t9 notification=- attempt=-" in out and "tenant=- notification=- attempt=-" in out


def test_configure_logging_twice_does_not_duplicate_lines(capsys):
    _capture(LoggingSettings(json=False), capsys)
    log = _capture(LoggingSettings(json=False), capsys)
    log.warning("once")
    assert capsys.readouterr().err.count("once") == 1
