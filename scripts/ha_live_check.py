"""Check the integration in a running, onboarded Home Assistant (HAOS, Container or Core) over its API.

    HA_TOKEN=<access token> python3 scripts/ha_live_check.py <ha url> <command> [args]

      errors                      the user step's refusals: bad URLs, credentials in a URL, no
                                  token, no source, an unreachable admin UI; each must show its error
      discovered                  confirm the Firefox Sync add-on's discovered flow (hassio_confirm)
      add <json form>             add an entry by hand (the user step's form, as the frontend sends it)
      entries                     the entries of firefox_sync and their state
      states <entry id> [want]    the entry's entities and their states; want: a JSON object of
                                  entity_ids (or substrings without a domain) to states ("*":
                                  anything but unavailable/unknown)
      diagnostics <entry id> <secret>...  the entry's diagnostics, none of the secrets in them
      reconfigure <entry id>      the reconfigure step with the entry's own values: it must finish
      lifecycle <entry id>        reload, disable (unloaded, entities unavailable), enable (loaded)
      reauth <entry id> <wrong> <right>  the reauth flow the entry started: the wrong token refused,
                                  then the right one taken, and the entry loaded again
      duplicate <json form>       the same source again: refused (already_configured)

Standard library only. Prints PASS/FAIL lines; the exit status is the number of failures.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = sys.argv[1].rstrip("/")
TOKEN = os.environ["HA_TOKEN"]
DOMAIN = "firefox_sync"
FAILURES = 0


def check(ok: bool, what: str, detail: object = "") -> bool:
    global FAILURES  # noqa: PLW0603
    FAILURES += not ok
    print(f"{'PASS' if ok else 'FAIL'}: {what}{f' ({detail})' if detail != '' else ''}")
    return ok


def call(method: str, path: str, body: object = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as res:
            text = res.read().decode()
            return res.status, json.loads(text) if text else None
    except urllib.error.HTTPError as err:
        text = err.read().decode()
        try:
            return err.code, json.loads(text)
        except ValueError:
            return err.code, text


def ws(message: dict):
    """One command over the websocket API (a small RFC 6455 client)."""
    url = urllib.parse.urlsplit(BASE)
    host, port = url.hostname, url.port or 80
    sock = socket.create_connection((host, port), timeout=60)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall(
        f"GET /api/websocket HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
    )
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += sock.recv(4096)
    buf = buf.split(b"\r\n\r\n", 1)[1]

    def send(obj: dict) -> None:
        payload = json.dumps(obj).encode()
        mask = os.urandom(4)
        n = len(payload)
        if n < 126:
            head = bytes([0x81, 0x80 | n])
        elif n < 65536:
            head = bytes([0x81, 0x80 | 126]) + struct.pack(">H", n)
        else:
            head = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", n)
        sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv() -> dict:
        nonlocal buf

        def need(k: int) -> None:
            nonlocal buf
            while len(buf) < k:
                buf += sock.recv(65536)

        need(2)
        n, off = buf[1] & 0x7F, 2
        if n == 126:
            need(4)
            n, off = struct.unpack(">H", buf[2:4])[0], 4
        elif n == 127:
            need(10)
            n, off = struct.unpack(">Q", buf[2:10])[0], 10
        need(off + n)
        frame, buf = buf[off : off + n], buf[off + n :]
        return json.loads(frame)

    recv()
    send({"type": "auth", "access_token": TOKEN})
    assert recv()["type"] == "auth_ok"
    send({"id": 1, **message})
    while True:
        res = recv()
        if res.get("id") == 1:
            sock.close()
            if not res.get("success"):
                raise RuntimeError(res)
            return res["result"]


def start_flow(context: dict | None = None):
    body = {"handler": DOMAIN, "show_advanced_options": False, **(context or {})}
    status, flow = call("POST", "/api/config/config_entries/flow", body)
    assert status == 200, (status, flow)
    return flow


def submit(flow_id: str, data: dict):
    return call("POST", f"/api/config/config_entries/flow/{flow_id}", data)[1]


def entry(entry_id: str) -> dict:
    _, entries = call("GET", f"/api/config/config_entries/entry?domain={DOMAIN}")
    return next(e for e in entries if e["entry_id"] == entry_id)


def entity_states(entry_id: str) -> list[dict]:
    ids = {e["entity_id"] for e in ws({"type": "config/entity_registry/list"}) if e["config_entry_id"] == entry_id}
    _, states = call("GET", "/api/states")
    return sorted((s for s in states if s["entity_id"] in ids), key=lambda s: s["entity_id"]), ids


def cmd_errors() -> None:
    unreachable = "http://192.0.2.1:9"  # TEST-NET-1: nothing answers
    cases = [
        ("a URL that is not one", {"url": "not a url", "metrics_token": "x"}, "url", "invalid_url"),
        ("an ftp:// URL", {"url": "ftp://example.com", "metrics_token": "x"}, "url", "invalid_url"),
        (
            "user and password in the admin URL",
            {"url": "https://u:p@example.com", "metrics_token": "x"},
            "url",
            "url_credentials",
        ),
        ("a user in the Sync URL", {"sync_url": "https://user@sync.example.com"}, "sync_url", "url_credentials"),
        ("the admin URL without its token", {"url": "https://example.com"}, "metrics_token", "token_required"),
        (
            "the accounts' URL without the Sync URL",
            {"auth_url": "https://api.example.com"},
            "sync_url",
            "sync_url_required",
        ),
        ("an empty form", {}, "base", "no_source"),
        ("an admin UI that does not answer", {"url": unreachable, "metrics_token": "x"}, "base", "cannot_connect"),
        ("a Sync server that does not answer", {"sync_url": unreachable}, "sync_url", "service_down"),
    ]
    for title, form, field, want in cases:
        flow = start_flow()
        res = submit(flow["flow_id"], {**form, "connection": {"verify_ssl": True}})
        got = (res or {}).get("errors") or {}
        check(res.get("type") == "form" and got.get(field) == want, f"refused: {title}", f"{field}: {got.get(field)}")
        call("DELETE", f"/api/config/config_entries/flow/{flow['flow_id']}")


def cmd_discovered() -> None:
    flows = [f for f in ws({"type": "config_entries/flow/progress"}) if f["handler"] == DOMAIN]
    hassio = [f for f in flows if f["context"].get("source") == "hassio"]
    if not check(bool(hassio), "the add-on's discovery started a flow", [f["step_id"] for f in flows]):
        return
    res = submit(hassio[0]["flow_id"], {})
    check(
        res.get("type") == "create_entry",
        "discovery confirmed",
        f"{res.get('title')} {res.get('result', {}).get('entry_id')}",
    )


def cmd_add(form: str) -> None:
    flow = start_flow()
    res = submit(flow["flow_id"], json.loads(form))
    check(res.get("type") == "create_entry", "entry added", res.get("result", {}).get("entry_id") or res.get("errors"))


def cmd_duplicate(form: str) -> None:
    flow = start_flow()
    res = submit(flow["flow_id"], json.loads(form))
    check(
        res.get("type") == "abort" and res.get("reason") == "already_configured",
        "the same source refused",
        res.get("reason"),
    )


def cmd_entries() -> None:
    _, entries = call("GET", f"/api/config/config_entries/entry?domain={DOMAIN}")
    for e in entries:
        print(f"{e['entry_id']} {e['state']:12} {e['source']:7} {e['title']} {e.get('reason') or ''}")


def cmd_states(entry_id: str, want: str = "{}") -> None:
    states, ids = entity_states(entry_id)
    print(f"-- {len(ids)} entities")
    for s in states:
        unit = s["attributes"].get("unit_of_measurement", "")
        print(f"   {s['entity_id']:68} {s['state']:>26} {unit}")
    for needle, value in json.loads(want).items():
        # an entity_id (with its domain) exactly, else a substring of the entity_ids
        match = [
            s["state"] for s in states if (s["entity_id"] == needle if "." in needle else needle in s["entity_id"])
        ]
        ok = bool(match) and all((v == value) if value != "*" else v not in ("unavailable", "unknown") for v in match)
        check(ok, f"{needle} = {value}", match)


def cmd_diagnostics(entry_id: str, *secrets: str) -> None:
    status, diag = call("GET", f"/api/diagnostics/config_entry/{entry_id}")
    text = json.dumps(diag)
    leaked = [s[:4] + "..." for s in secrets if s and s in text]
    check(
        status == 200 and not leaked and "PRIVATE KEY" not in text,
        "diagnostics without the secrets",
        f"HTTP {status}, leaked {leaked}",
    )
    print(json.dumps(diag, indent=1)[:3000])


def cmd_reconfigure(entry_id: str) -> None:
    flow = start_flow({"entry_id": entry_id})
    check(flow.get("step_id") == "reconfigure", "the reconfigure step opens", flow.get("step_id") or flow)
    current = entry(entry_id)

    # the form's suggested values are the entry's own: submit them back
    def suggested(fields: list[dict]) -> dict:
        values = {}
        for field in fields:
            if field.get("type") == "expandable":  # a section: its own fields
                values[field["name"]] = suggested(field["schema"])
            elif (value := (field.get("description") or {}).get("suggested_value")) is not None:
                values[field["name"]] = value
        return values

    values = suggested(flow["data_schema"])
    res = submit(flow["flow_id"], values)
    check(
        res.get("type") == "abort" and res.get("reason") == "reconfigure_successful",
        "reconfigured",
        res.get("reason") or res.get("errors"),
    )
    texts = ws({"type": "frontend/get_translations", "language": "en", "category": "config", "integration": [DOMAIN]})
    key = f"component.{DOMAIN}.config.abort.reconfigure_successful"
    check(key in texts["resources"], "its abort has a text", texts["resources"].get(key))
    time.sleep(5)
    check(entry(entry_id)["state"] == "loaded", "loaded again", current["title"])


def wait_state(entry_id: str, want: str, seconds: int = 60) -> str:
    state = ""
    for _ in range(seconds):
        state = entry(entry_id)["state"]
        if state == want:
            break
        time.sleep(1)
    return state


def cmd_lifecycle(entry_id: str) -> None:
    status, res = call("POST", f"/api/config/config_entries/entry/{entry_id}/reload")
    check(status == 200 and wait_state(entry_id, "loaded") == "loaded", "reloaded", res)
    ws({"type": "config_entries/disable", "entry_id": entry_id, "disabled_by": "user"})
    check(wait_state(entry_id, "not_loaded") == "not_loaded", "disabled: unloaded")
    states, ids = entity_states(entry_id)
    check(
        all(s["state"] == "unavailable" for s in states),
        "its entities unavailable or gone",
        f"{len(states)} of {len(ids)} with a state",
    )
    ws({"type": "config_entries/disable", "entry_id": entry_id, "disabled_by": None})
    check(wait_state(entry_id, "loaded", 120) == "loaded", "enabled: loaded again")


def cmd_reauth(entry_id: str, wrong: str, right: str) -> None:
    flows = []
    for _ in range(150):
        flows = [
            f
            for f in ws({"type": "config_entries/flow/progress"})
            if f["handler"] == DOMAIN
            and f["context"].get("source") == "reauth"
            and f["context"].get("entry_id") == entry_id
        ]
        if flows:
            break
        time.sleep(2)
    if not check(bool(flows), "the entry asks for a new token (reauth)", entry(entry_id)["state"]):
        return
    flow_id = flows[0]["flow_id"]
    res = submit(flow_id, {"metrics_token": wrong})
    check((res.get("errors") or {}).get("base") == "invalid_auth", "a wrong token refused", res.get("errors"))
    res = submit(flow_id, {"metrics_token": right})
    check(
        res.get("type") == "abort" and res.get("reason") == "reauth_successful",
        "the right token taken",
        res.get("reason") or res.get("errors"),
    )
    texts = ws({"type": "frontend/get_translations", "language": "en", "category": "config", "integration": [DOMAIN]})
    key = f"component.{DOMAIN}.config.abort.reauth_successful"
    check(key in texts["resources"], "its abort has a text", texts["resources"].get(key))
    check(wait_state(entry_id, "loaded") == "loaded", "loaded again")


def main() -> int:
    commands = {
        "errors": cmd_errors,
        "discovered": cmd_discovered,
        "add": cmd_add,
        "duplicate": cmd_duplicate,
        "entries": cmd_entries,
        "states": cmd_states,
        "diagnostics": cmd_diagnostics,
        "reconfigure": cmd_reconfigure,
        "lifecycle": cmd_lifecycle,
        "reauth": cmd_reauth,
    }
    commands[sys.argv[2]](*sys.argv[3:])
    return FAILURES


if __name__ == "__main__":
    sys.exit(main())
