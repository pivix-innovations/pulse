"""The shipped alert rules and dashboard stay well-formed and refer only to metrics Pulse defines."""
from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.apply_openobserve import ROOT, alert_payload

METRICS_SRC = (Path(__file__).resolve().parent.parent / "telemetry" / "metrics.py").read_text()
# "pulse.provider.duration" -> pulse_provider_duration; counters and histograms add suffixes in queries
DEFINED = {m.replace(".", "_") for m in re.findall(r'"(pulse\.[a-z_.]+)"', METRICS_SRC)}


def _base_metrics(promql: str) -> set[str]:
    return {re.sub(r"_(bucket|count|sum|total)$", "", m) if m not in DEFINED else m for m in re.findall(r"pulse_[a-z_]+", promql)}


def test_eight_alert_rules_with_unique_names_and_valid_payloads():
    rules = json.loads((ROOT / "alerts.json").read_text())["alerts"]
    assert len(rules) == 8 and len({r["name"] for r in rules}) == 8
    for r in rules:
        p = alert_payload(r, "dest")
        assert p["destinations"] == ["dest"] and p["stream_type"] == "metrics"
        assert p["query_condition"]["promql"] == r["promql"]
        assert p["trigger_condition"]["period"] >= 1


def test_alerts_and_dashboard_only_query_metrics_pulse_defines():
    rules = json.loads((ROOT / "alerts.json").read_text())["alerts"]
    dash = json.loads((ROOT / "dashboard.json").read_text())
    queries = [r["promql"] for r in rules] + [
        q["query"] for t in dash["tabs"] for p in t["panels"] for q in p["queries"]
    ]
    unknown = {m for q in queries for m in _base_metrics(q)} - DEFINED
    assert unknown == set()
