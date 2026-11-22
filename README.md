# Firefox Sync for Home Assistant

[![Open your Home Assistant instance and open this repository.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=porelli&repository=ha-firefox-sync&category=integration)

A custom integration for a self-hosted Firefox Sync server of
[firefox-sync](https://github.com/porelli/firefox-sync), wherever it runs: the Firefox Sync
add-on of [porelli/hassio-addons](https://github.com/porelli/hassio-addons), docker compose,
Podman Quadlets, Kubernetes, or another host (see [Without the add-on](#without-the-add-on)).

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

Home Assistant 2026.3 or later (the integration's own brand images need it).

**With HACS, or the Marketplace of Home Assistant 2026.11 and later** (HACS built in, under
**Settings > Marketplace**): until it is in the default catalog, add this repository as a
custom repository. The button above does it (the same link opens HACS before 2026.11 and the
Marketplace after); by hand, in HACS: the menu > **Custom repositories**,
`https://github.com/porelli/ha-firefox-sync`, type **Integration**. Then download
**Firefox Sync**. HACS needs a GitHub account for custom repositories; it installs the latest
release, and offers the next ones under **Settings > Updates**. Restart Home Assistant when it
asks (a first install of an integration with a config flow loads without one on 2026.11).

**By hand**: copy `custom_components/firefox_sync/` of the
[latest release](https://github.com/porelli/ha-firefox-sync/releases/latest) into the
`custom_components/` folder of your Home Assistant configuration (next to
`configuration.yaml`; on Home Assistant OS through the Samba or SSH add-on, in a container
the volume mounted on `/config`), so that `custom_components/firefox_sync/manifest.json`
exists, and restart Home Assistant. Updates: the same, over the old folder.

Then:

- **With the Firefox Sync add-on**: Home Assistant shows the discovered **Firefox Sync** under
  **Settings > Devices & services**: accept it. The add-on sends its admin UI's address and
  token itself; the device links to the add-on's admin UI in the sidebar.
- **Otherwise**: **Settings > Devices & services > Add integration > Firefox Sync**, and fill
  in the form below.

Several entries are fine: one per stack.

## Without the add-on

The integration only reads HTTP(S) endpoints, so the stack can run anywhere Home Assistant
reaches: another host with docker compose or Podman, a Kubernetes cluster (the Helm chart), a
VPS. Home Assistant can be any installation (OS, Container, Core). Give it either:

- the **admin UI** (every entity): its URL as Home Assistant reaches it, and the metrics
  token. With compose the admin UI listens on `ADMIN_EXPORT_PORT` of `.env`,
  `127.0.0.1:8090` by default (the server itself only): set it to an address Home Assistant
  reaches (for example `192.168.1.10:8090`), or put the admin UI behind your reverse proxy.
  Mind firefox-sync's [signing-in](https://github.com/porelli/firefox-sync/blob/main/docs/admin/signing-in.md)
  page before publishing it: the trusted header must not be used then; or
- only the **public URLs** that Firefox uses (no admin UI needed): the health and version of
  each service.

With HTTPS from a private CA or client certificates in front of the stack, see
**Connection** below.

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

- `scripts/ha_core_check.py` drives a fresh Home Assistant Core in Docker (onboarding, the
  config flow over REST, the states, the diagnostics), for testing against a running stack.
- `scripts/ha_live_check.py` checks an onboarded Home Assistant (Home Assistant OS too): the
  flow's refusals, the add-on's discovery, entities, diagnostics, reconfigure, reauth,
  reload/disable/enable.
- `scripts/hacs_check.py <clone of hacs/integration>` runs the HACS action's checks that
  need no GitHub repository, with HACS's own schemas.

CI (`.github/workflows/validate.yml`): hassfest, the HACS action (category integration,
nothing ignored), the tests and ruff, on every push, pull request, and every night.

Dependabot (`.github/dependabot.yml`) updates the actions weekly. Its pull requests are merged by
GitHub's auto-merge (`.github/workflows/dependabot-automerge.yml`, squash, majors included) once the
checks a ruleset of `main` requires pass: HACS, Tests and lint, hassfest. Repository admins bypass
the ruleset, so a direct push to `main` still works. To stop it: disable that workflow, or turn off
*Allow auto-merge* in the settings.

## Releases

HACS and the Marketplace install GitHub releases (a tag alone is not enough; without any
release they would install the default branch's latest commit). To release X.Y.Z:

1. Set `version` in `custom_components/firefox_sync/manifest.json` and in `pyproject.toml`
   to `X.Y.Z` (a pre-release: `X.Y.ZbN` or `X.Y.ZrcN`). If the release needs a newer Home
   Assistant, raise `homeassistant` in `hacs.json`. Commit.
1. `git tag vX.Y.Z && git push origin vX.Y.Z`.
1. `.github/workflows/release.yml` checks that the tag is `v` + the manifest's version and
   that pyproject agrees, runs the tests and hassfest, then publishes the GitHub release
   (notes generated from the commits: edit them on GitHub, users read them in the update
   entity; a version with a letter is marked as a pre-release).

Users get it under **Settings > Updates** within hours (HACS's catalog data is regenerated
every few hours; custom repositories are checked through the GitHub API).
