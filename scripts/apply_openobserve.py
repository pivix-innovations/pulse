"""
Apply Pulse's alert rules and overview dashboard to OpenObserve (idempotent: re-running updates in place).

    uv run python -m scripts.apply_openobserve [--dry-run] [--destination NAME] [--no-alerts] [--no-dashboard]

Environment (defaults match docker-compose.yml's dev OpenObserve):
    PULSE_O2_URL            http://localhost:5080
    PULSE_O2_ORG            default
    PULSE_O2_EMAIL / PULSE_O2_PASSWORD
    PULSE_ALERT_WEBHOOK_URL webhook that receives alerts; only needed when the destination does not exist yet
                            (OpenObserve refuses localhost/private URLs for destinations)
The rules live in ops/openobserve/alerts.json and the dashboard in ops/openobserve/dashboard.json.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent / "ops" / "openobserve"
DEFAULT_DESTINATION = "pulse-alerts"


def alert_payload(rule: dict[str, Any], destination: str) -> dict[str, Any]:
    """OpenObserve v2 scheduled PromQL alert for one rule from alerts.json."""
    return {
        "name": rule["name"],
        "description": rule["description"],
        "stream_type": "metrics",
        "stream_name": rule["stream_name"],
        "is_real_time": False,
        "enabled": True,
        "query_condition": {
            "type": "promql",
            "promql": rule["promql"],
            "promql_condition": {"column": "value", "operator": rule["operator"], "value": rule["value"]},
        },
        "trigger_condition": {
            "period": rule["period_minutes"],
            "operator": ">=",
            "threshold": 1,
            "frequency": 1,
            "frequency_type": "minutes",
            "silence": rule["silence_minutes"],
        },
        "destinations": [destination],
    }


class O2Error(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class O2:
    def __init__(self, url: str, org: str, email: str, password: str) -> None:
        self.base = url.rstrip("/")
        self.org = org
        token = base64.b64encode(f"{email}:{password}".encode()).decode()
        self._headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}

    def call(self, method: str, path: str, body: Any = None) -> Any:
        req = urllib.request.Request(
            f"{self.base}{path}", method=method, headers=self._headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raise O2Error(exc.code, f"{method} {path} -> {exc.code}: {exc.read().decode()[:300]}")
        except urllib.error.URLError as exc:
            raise SystemExit(f"Cannot reach OpenObserve at {self.base}: {exc.reason}")
        return json.loads(raw) if raw else None


def ensure_destination(o2: O2, name: str, dry_run: bool) -> None:
    existing = o2.call("GET", f"/api/{o2.org}/alerts/destinations") or []
    if any(d.get("name") == name for d in existing):
        print(f"destination '{name}': exists")
        return
    url = os.environ.get("PULSE_ALERT_WEBHOOK_URL")
    if not url:
        raise SystemExit(
            f"Destination '{name}' does not exist. Set PULSE_ALERT_WEBHOOK_URL (a public webhook, e.g. Slack) "
            "so it can be created, or create it in OpenObserve and pass --destination."
        )
    print(f"destination '{name}': creating -> {url}")
    if not dry_run:
        o2.call("POST", f"/api/{o2.org}/alerts/destinations", {
            "name": name, "url": url, "method": "post", "skip_tls_verify": False,
            "template": "o2_default_content", "headers": {},
        })


def apply_alerts(o2: O2, destination: str, dry_run: bool) -> None:
    rules = json.loads((ROOT / "alerts.json").read_text())["alerts"]
    if not dry_run:
        ensure_destination(o2, destination, dry_run)
    existing = {a["name"]: a["alert_id"] for a in (o2.call("GET", f"/api/v2/{o2.org}/alerts") or {}).get("list", [])}
    for rule in rules:
        payload = alert_payload(rule, destination)
        verb = "update" if rule["name"] in existing else "create"
        print(f"alert {rule['name']}: {verb}")
        if dry_run:
            continue
        try:
            if verb == "update":
                o2.call("PUT", f"/api/v2/{o2.org}/alerts/{existing[rule['name']]}", payload)
            else:
                o2.call("POST", f"/api/v2/{o2.org}/alerts", payload)
        except O2Error as exc:
            if exc.status != 404 or "Stream" not in str(exc):
                raise
            # A metric only exists in OpenObserve after its first data point.
            print(f"  skipped: stream '{rule['stream_name']}' has no data yet; re-run once Pulse has reported it")


def apply_dashboard(o2: O2, dry_run: bool) -> None:
    dashboard = json.loads((ROOT / "dashboard.json").read_text())
    listed = (o2.call("GET", f"/api/{o2.org}/dashboards") or {}).get("dashboards", [])
    found = next((d for d in listed if (d.get("v5") or {}).get("title") == dashboard["title"]), None)
    print(f"dashboard '{dashboard['title']}': {'update' if found else 'create'}")
    if dry_run:
        return
    if found:
        dash_id = found["v5"]["dashboardId"]
        o2.call("PUT", f"/api/{o2.org}/dashboards/{dash_id}?hash={found.get('hash', '')}", {**dashboard, "dashboardId": dash_id})
    else:
        o2.call("POST", f"/api/{o2.org}/dashboards", dashboard)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="show what would change without writing")
    parser.add_argument("--destination", default=DEFAULT_DESTINATION, help="alert destination name")
    parser.add_argument("--no-alerts", action="store_true")
    parser.add_argument("--no-dashboard", action="store_true")
    args = parser.parse_args(argv)

    o2 = O2(
        os.environ.get("PULSE_O2_URL", "http://localhost:5080"),
        os.environ.get("PULSE_O2_ORG", "default"),
        os.environ.get("PULSE_O2_EMAIL", "admin@pulse.local"),
        os.environ.get("PULSE_O2_PASSWORD", "Pulse-Dev-2026!"),
    )
    try:
        if not args.no_alerts:
            apply_alerts(o2, args.destination, args.dry_run)
        if not args.no_dashboard:
            apply_dashboard(o2, args.dry_run)
    except O2Error as exc:
        raise SystemExit(str(exc))


if __name__ == "__main__":
    sys.exit(main())
