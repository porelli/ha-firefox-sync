# Firefox Sync for Home Assistant

A custom integration (HACS) for a self-hosted Firefox Sync server of
[firefox-sync](https://github.com/porelli/firefox-sync), wherever it runs: the Firefox Sync
add-on of [porelli/hassio-addons](https://github.com/porelli/hassio-addons), docker compose,
Podman Quadlets, Kubernetes, or another host.

## What it gives you

One device per entry, with the entities of what you give it:

| Source | Entities |
|---|---|
| **the admin UI's metrics** (`/metrics` with `ADMIN_METRICS_TOKEN`) | accounts and max users (near the limit, full), stored items and bytes, the size of each database, the last good backup and whether the last one failed, the Sync error rate and its spike alert, the certificates' expiry, the self-hosted accounts and their limit, the alerts that failed per destination, and every component the admin UI checks (connectivity) |
| **the public URLs** (no admin UI needed) | each service up or down (connectivity), its database where its heartbeat says (the Sync server's, the accounts API's, the profile server's), and its version |

Both in one entry work too: the URLs' view of a service replaces the admin UI's for the same
component (one entity each), and the databases come from the metrics, which have them one by
one. The device's software version is the Sync server's.

## Install

1. HACS > the menu > **Custom repositories**: `https://github.com/porelli/ha-firefox-sync`,
   type **Integration**. Install **Firefox Sync**, restart Home Assistant.
1. **Settings > Devices & services > Add integration > Firefox Sync**, or, with the Firefox
   Sync add-on, accept the discovered one: the add-on sends its admin UI's address and token
   itself.

Several entries are fine: one per stack.

## Setting it up by hand

The form has two parts; fill either or both.

**The admin UI's metrics**

- **Admin UI URL**: where Home Assistant reaches the admin UI, for example
  `http://192.168.1.10:8090` (compose's `ADMIN_EXPORT_PORT`; it listens on `127.0.0.1` by
  default: publish it on an address Home Assistant reaches, or go through your reverse proxy),
  or `https://admin.example.com`.
- **Metrics token**: `ADMIN_METRICS_TOKEN` of the stack's `.env` (Helm:
  `admin.metrics.token`). Without it the admin UI has no `/metrics` (it answers 404).

**The public URLs** (basic mode, without the admin UI)

- **Sync server URL** (required in this mode): `SYNC_PUBLIC_URL`, for example
  `https://sync.example.com`.
- Optional, with self-hosted accounts: the accounts API (`FXA_AUTH_URL`), pages
  (`FXA_CONTENT_URL`), profile server (`FXA_PROFILE_URL`) and pairing server
  (`FXA_PAIRING_URL`, `wss://...`).

The integration asks each URL's Dockerflow endpoints every minute: `/__lbheartbeat__` (the
process answers), `/__heartbeat__` (its database: syncstorage-rs says `"database": "Ok"`, the
accounts' API and profile server answer 503 without theirs) and `/__version__`.

**Connection** (for every URL of the entry)

| Option | What it does |
|---|---|
| Verify TLS certificates | on by default. Off only for a test setup: anybody between Home Assistant and the stack could then read the metrics token |
| Custom CA | a private CA's certificate (Caddy's internal CA, step-ca, your own), PEM text pasted, or the path of a file Home Assistant can read (`/config/ffs-ca.pem`, `/ssl/...`) |
| Client certificate and key | for [client certificates](https://github.com/porelli/firefox-sync/blob/main/docs/access-control.md#client-certificates) in front of the stack: the certificate and its key, PEM, pasted or a path. The key must not be encrypted |

The certificates and the key are kept in the config entry (Home Assistant's `.storage`, in its
backups); the diagnostics leave them out, with the token.

## Behind single sign-on

With a sign-in proxy (Authelia, Authentik, oauth2-proxy...) in front of the admin UI or the
accounts pages, as in firefox-sync's
[access control](https://github.com/porelli/firefox-sync/blob/main/docs/access-control.md), let
these paths through without a sign-in, as that page already does for what Firefox fetches in the
background:

- `/metrics` of the admin UI: Home Assistant sends no cookie, only the token, which already
  protects it (the admin UI answers 404 to a request without it);
- `/__heartbeat__`, `/__lbheartbeat__` and `/__version__` of each URL you give: they say
  only whether the service and its database are up, and which version it runs.

Otherwise the integration sees the sign-in page's redirect: the URL counts as down (the setup
says `HTTP 302`). With client certificates on those hosts, give the integration a certificate
instead (above); firefox-sync's test setup asks for one on the admin UI and lets the
heartbeats of the accounts pages through.

## Diagnostics

**Settings > Devices & services > Firefox Sync > the entry > Download diagnostics**: the entry
(the token, the key and the certificates redacted), the last metrics (without the Sync
server's statsd counters) and each URL's heartbeat.

## Development

```bash
uv venv -p 3.14 && uv pip install pytest-homeassistant-custom-component ruff
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

`scripts/ha_core_check.py` drives a real Home Assistant Core in Docker (onboarding, the config
flow over REST, the states, the diagnostics), for testing against a running stack.
