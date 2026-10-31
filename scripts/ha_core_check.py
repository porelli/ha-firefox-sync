"""Drive a real Home Assistant Core (docker, no Supervisor) with this integration installed.

Onboards a fresh instance, adds config entries through the config flow REST API, waits for
the coordinator's first rounds and prints the entities of each entry with their states.
Standard library only, for the test VM's own Python.

    python3 scripts/ha_core_check.py <ha url> <entries.json> [seconds to wait]

entries.json: a list of {"title": ..., "data": <the user step's form>, "expect": {...}}; the
form as the frontend sends it (the connection options in their "connection" section).
"expect" maps entity_id substrings to the state they must have; "expect_error" instead
expects the flow to show that error. Exit status 1 when an expectation fails.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1].rstrip("/")
ENTRIES = json.load(open(sys.argv[2]))
WAIT = int(sys.argv[3]) if len(sys.argv) > 3 else 90
CLIENT_ID = f"{BASE}/"


def call(method: str, path: str, body=None, token: str | None = None, form: bool = False):
    data = None
    headers = {}
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            text = res.read().decode()
            return res.status, (json.loads(text) if text else None)
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode()


def onboard() -> str:
    for _ in range(120):
        try:
            status, _ = call("GET", "/api/onboarding")
            if status == 200:
                break
        except OSError:
            pass
        time.sleep(2)
    status, res = call(
        "POST",
        "/api/onboarding/users",
        {"client_id": CLIENT_ID, "name": "Test", "username": "test", "password": "test-password-123", "language": "en"},
    )
    assert status == 200, (status, res)
    status, tokens = call(
        "POST",
        "/auth/token",
        {"grant_type": "authorization_code", "code": res["auth_code"], "client_id": CLIENT_ID},
        form=True,
    )
    assert status == 200, (status, tokens)
    token = tokens["access_token"]
    call("POST", "/api/onboarding/core_config", {}, token)
    call("POST", "/api/onboarding/analytics", {}, token)
    call(
        "POST",
        "/api/onboarding/integration",
        {"client_id": CLIENT_ID, "redirect_uri": f"{BASE}/?auth_callback=1"},
        token,
    )
    return token


def main() -> int:
    token = onboard()
    print(f"onboarded {BASE}")
    failures = 0
    created = []
    for entry in ENTRIES:
        status, flow = call(
            "POST",
            "/api/config/config_entries/flow",
            {"handler": "firefox_sync", "show_advanced_options": False},
            token,
        )
        assert status == 200, (status, flow)
        status, result = call("POST", f"/api/config/config_entries/flow/{flow['flow_id']}", entry["data"], token)
        if "expect_error" in entry:
            got = (result or {}).get("errors") if isinstance(result, dict) else result
            ok = isinstance(got, dict) and entry["expect_error"] in got.values()
            print(
                f"{'PASS' if ok else 'FAIL'}: {entry['title']}: the flow refuses it ({got}, {result.get('description_placeholders') if isinstance(result, dict) else ''})"
            )
            failures += not ok
            continue
        if not isinstance(result, dict) or result.get("type") != "create_entry":
            print(f"FAIL: {entry['title']}: no entry: {result}")
            failures += 1
            continue
        print(f"PASS: {entry['title']}: entry created ({result['result']['entry_id']}, {result['title']})")
        created.append((entry, result["result"]["entry_id"]))
    time.sleep(WAIT)
    status, states = call("GET", "/api/states", token=token)
    for entry, entry_id in created:
        prefix = entry["object_prefix"]
        mine = sorted(
            (s for s in states if s["entity_id"].split(".", 1)[1].startswith(prefix)), key=lambda s: s["entity_id"]
        )
        print(f"-- {entry['title']}: {len(mine)} entities")
        for s in mine:
            unit = s["attributes"].get("unit_of_measurement", "")
            dc = s["attributes"].get("device_class", "")
            print(f"   {s['entity_id']:70} {s['state']:>14} {unit:4} {dc}")
        for needle, want in entry.get("expect", {}).items():
            match = [s for s in mine if needle in s["entity_id"]]
            ok = bool(match) and all(
                (s["state"] == want) if want != "*" else s["state"] not in ("unavailable", "unknown") for s in match
            )
            print(f"{'PASS' if ok else 'FAIL'}: {entry['title']}: {needle} = {want} ({[s['state'] for s in match]})")
            failures += not ok
        status, diag = call("GET", f"/api/diagnostics/config_entry/{entry_id}", token=token)
        text = json.dumps(diag)
        leaked = [
            v
            for v in (entry["data"].get("metrics_token"), (entry["data"].get("connection") or {}).get("client_key"))
            if v and v in text
        ]
        ok = status == 200 and not leaked and ("BEGIN PRIVATE KEY" not in text)
        print(
            f"{'PASS' if ok else 'FAIL'}: {entry['title']}: diagnostics without the token and the key (HTTP {status})"
        )
        failures += not ok
    print(f"== {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
