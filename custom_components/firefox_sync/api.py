"""What the integration asks of a firefox-sync stack, over HTTP(S).

Two sources, each optional, one entry can have both:
- the admin UI's /metrics, with its token (ADMIN_METRICS_TOKEN): every entity;
- the public URLs of the services (the Sync server, and the self-hosted accounts' API,
  pages, profile and pairing servers): their Dockerflow endpoints, /__lbheartbeat__ (the
  process answers), /__heartbeat__ (with its database, where it has one) and /__version__.

The connection options apply to both: TLS verification on or off, a custom CA, and a
client certificate with its key (the client certificates of docs/access-control.md).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
import json
from pathlib import Path
import ssl
import tempfile
from typing import Any

import aiohttp

from .const import (
    CONF_CA_CERT,
    CONF_CLIENT_CERT,
    CONF_CLIENT_KEY,
    CONF_VERIFY_SSL,
    REQUEST_TIMEOUT,
)
from .prometheus import Metrics, PrometheusParseError, parse


class MetricsAuthError(Exception):
    """The admin UI refused the token (it answers 404 to a wrong token, or with metrics off)."""


class MetricsConnectionError(Exception):
    """The admin UI could not be reached, or did not answer with metrics."""


class TLSConfigError(Exception):
    """The CA, the client certificate or its key cannot be loaded."""

    def __init__(self, field_name: str, detail: str) -> None:
        """The field (ca_cert, client_cert, client_key) and what is wrong with it."""
        super().__init__(f"{field_name}: {detail}")
        self.field = field_name


def _pem_or_path(value: str, field_name: str) -> str:
    """The PEM text of a field: pasted as it is, or read from a path."""
    value = value.strip()
    if value.startswith("-----BEGIN"):
        return value + "\n"
    path = Path(value)
    try:
        text = path.read_text()
    except OSError as err:
        raise TLSConfigError(field_name, f"not PEM text, and {value} cannot be read: {err.strerror}") from err
    if "-----BEGIN" not in text:
        raise TLSConfigError(field_name, f"{value} holds no PEM data")
    return text


def build_ssl_context(options: Mapping[str, Any]) -> ssl.SSLContext | bool:
    """The ssl argument of the requests: True (the system's CAs), False (no check), or a context.

    Blocking (it reads files): run it in the executor.
    """
    verify = options.get(CONF_VERIFY_SSL, True)
    ca = (options.get(CONF_CA_CERT) or "").strip()
    cert = (options.get(CONF_CLIENT_CERT) or "").strip()
    key = (options.get(CONF_CLIENT_KEY) or "").strip()
    if bool(cert) != bool(key):
        missing = CONF_CLIENT_KEY if cert else CONF_CLIENT_CERT
        raise TLSConfigError(missing, "a client certificate needs both the certificate and its key")
    if not ca and not cert:
        return bool(verify)
    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    if ca:
        try:
            context.load_verify_locations(cadata=_pem_or_path(ca, CONF_CA_CERT))
        except ssl.SSLError as err:
            raise TLSConfigError(CONF_CA_CERT, f"not a PEM certificate: {err.reason or err}") from err
    if cert:
        cert_pem = _pem_or_path(cert, CONF_CLIENT_CERT)
        key_pem = _pem_or_path(key, CONF_CLIENT_KEY)
        # load_cert_chain takes files only: a private directory, removed at once
        with tempfile.TemporaryDirectory(prefix="firefox_sync_") as tmp:
            cert_file, key_file = Path(tmp, "cert.pem"), Path(tmp, "key.pem")
            cert_file.write_text(cert_pem)
            key_file.touch(mode=0o600)
            key_file.write_text(key_pem)
            try:
                context.load_cert_chain(cert_file, key_file)
            except ssl.SSLError as err:
                raise TLSConfigError(
                    CONF_CLIENT_KEY,
                    f"the certificate and key do not load (an encrypted key is not supported): {err.reason or err}",
                ) from err
    return context


async def async_fetch_metrics(
    session: aiohttp.ClientSession,
    url: str,
    token: str,
    ssl_context: ssl.SSLContext | bool = True,
) -> Metrics:
    """Fetch and parse /metrics of the admin UI at url, with the bearer token."""
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            response = await session.get(
                f"{url.rstrip('/')}/metrics",
                headers={"Authorization": f"Bearer {token}"},
                allow_redirects=False,
                ssl=ssl_context,
            )
            if response.status in (HTTPStatus.NOT_FOUND, HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                raise MetricsAuthError(f"HTTP {response.status}")
            if response.status != HTTPStatus.OK:
                raise MetricsConnectionError(f"HTTP {response.status}")
            text = await response.text()
    except (TimeoutError, aiohttp.ClientError) as err:
        raise MetricsConnectionError(str(err) or type(err).__name__) from err
    try:
        metrics = parse(text)
    except PrometheusParseError as err:
        raise MetricsConnectionError(f"not Prometheus metrics: {err}") from err
    # the admin UI always sends its counters of the Sync server; a page without any ffs_
    # metric is something else at that address
    if not any(name.startswith("ffs_") for name in metrics.samples):
        raise MetricsConnectionError("no ffs_ metrics: not the firefox-sync admin UI")
    return metrics


@dataclass(frozen=True)
class Service:
    """A service with Dockerflow endpoints, by its public URL.

    component: its name, the admin UI's (ffs_up{component}); database: the name of its
    database's state, for the services whose /__heartbeat__ checks one.
    """

    key: str
    component: str
    database: str | None = None


# the names of the admin UI's components (admin/health.go), so that an entry with both the
# metrics and the URLs has one entity per component
SERVICES: dict[str, Service] = {
    "sync_url": Service("sync", "Sync server", "Sync server database"),
    "auth_url": Service("auth", "Accounts API", "Accounts database"),
    "content_url": Service("content", "Accounts pages"),
    "profile_url": Service("profile", "Profile server", "Profile server database"),
    "pairing_url": Service("pairing", "Pairing server"),
}


@dataclass
class ServiceHealth:
    """What the Dockerflow endpoints of one service said."""

    service: Service
    # the process answers (/__lbheartbeat__, else /__heartbeat__ with any status of its own)
    up: bool = False
    # its database (syncstorage-rs: "database" of the heartbeat; the accounts' API and
    # profile server: 200 or 503); None when it has none or it did not answer
    database: bool | None = None
    version: str | None = None
    error: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def http_base(url: str) -> str:
    """The http(s) URL of a service: the pairing server's wss:// as https://."""
    url = url.strip().rstrip("/")
    if url.startswith("wss://"):
        return "https://" + url[len("wss://") :]
    if url.startswith("ws://"):
        return "http://" + url[len("ws://") :]
    return url


def parse_heartbeat(service: Service, status: int, body: str) -> tuple[bool | None, dict[str, Any]]:
    """The state of the service's database from its /__heartbeat__, and the JSON it sent.

    syncstorage-rs: {"status": "Ok"|"Err", "database": "Ok"|"Err"|"Unknown", "version":
    ..., "quota": {...}}, 503 when the check of its database fails. The accounts' API and
    profile server: {} with 200 when their database answers, 503 when not. The accounts
    pages and the pairing server have no database.
    """
    try:
        data = json.loads(body) if body.strip() else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if service.database is None:
        return None, data
    if service.key == "sync":
        database = data.get("database")
        if isinstance(database, str):
            return database.lower() == "ok", data
        return status == HTTPStatus.OK, data
    if status == HTTPStatus.OK:
        return True, data
    if status == HTTPStatus.SERVICE_UNAVAILABLE:
        return False, data
    return None, data


def parse_version(body: str) -> str | None:
    """The version of a /__version__ (Dockerflow's version.json), None without one."""
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if isinstance(data, dict) and isinstance(data.get("version"), str):
        version = data["version"].strip()
        # Mozilla's channelserver builds say TBD there (admin/health.go)
        return version if version and version != "TBD" else None
    return None


async def _get(session: aiohttp.ClientSession, url: str, ssl_context: ssl.SSLContext | bool) -> tuple[int, str]:
    async with asyncio.timeout(REQUEST_TIMEOUT):
        response = await session.get(url, allow_redirects=False, ssl=ssl_context)
        return response.status, await response.text()


async def async_fetch_health(
    session: aiohttp.ClientSession,
    service: Service,
    url: str,
    ssl_context: ssl.SSLContext | bool = True,
) -> ServiceHealth:
    """Ask a service's Dockerflow endpoints; never raises: a failure is a service down."""
    base = http_base(url)
    health = ServiceHealth(service)
    try:
        lb_status, _ = await _get(session, f"{base}/__lbheartbeat__", ssl_context)
        hb_status, hb_body = await _get(session, f"{base}/__heartbeat__", ssl_context)
    except (TimeoutError, aiohttp.ClientError) as err:
        health.error = str(err) or type(err).__name__
        return health
    # an answer of the service itself: 200, or 503 for a database that is down. A redirect
    # (a sign-in proxy), 401/403 (client certificates) or 404 is something in front of it
    if lb_status == HTTPStatus.OK or hb_status in (HTTPStatus.OK, HTTPStatus.SERVICE_UNAVAILABLE):
        health.up = True
    else:
        health.error = f"HTTP {lb_status} from /__lbheartbeat__, {hb_status} from /__heartbeat__"
        return health
    if hb_status in (HTTPStatus.OK, HTTPStatus.SERVICE_UNAVAILABLE):
        health.database, health.details = parse_heartbeat(service, hb_status, hb_body)
    try:
        version_status, version_body = await _get(session, f"{base}/__version__", ssl_context)
    except (TimeoutError, aiohttp.ClientError):
        version_status, version_body = 0, ""
    if version_status == HTTPStatus.OK:
        health.version = parse_version(version_body)
    if health.version is None and isinstance(health.details.get("version"), str):
        # syncstorage-rs and the channelserver also say it in their heartbeat
        health.version = health.details["version"]
    return health
