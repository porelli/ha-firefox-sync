"""Tests of the requests: the Dockerflow endpoints and their formats, and the TLS options."""

from __future__ import annotations

import ssl

import aiohttp
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.firefox_sync.api import (
    SERVICES,
    TLSConfigError,
    async_fetch_health,
    build_ssl_context,
    http_base,
    parse_heartbeat,
    parse_version,
)
from custom_components.firefox_sync.const import (
    CONF_AUTH_URL,
    CONF_CA_CERT,
    CONF_CLIENT_CERT,
    CONF_CLIENT_KEY,
    CONF_CONTENT_URL,
    CONF_PAIRING_URL,
    CONF_PROFILE_URL,
    CONF_SYNC_URL,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .conftest import AUTH, SYNC, mock_service

SYNC_SERVICE = SERVICES[CONF_SYNC_URL]
AUTH_SERVICE = SERVICES[CONF_AUTH_URL]


# syncstorage-rs 0.21's handlers::heartbeat (syncserver/src/web/handlers.rs)
SYNC_OK = '{"database":"Ok","quota":{"enabled":false,"size":0},"status":"Ok","version":"0.21.1"}'
SYNC_DB_ERR = (
    '{"database":"Err","database_msg":"check failed without error",'
    '"quota":{"enabled":false,"size":0},"status":"Err","version":"0.21.1"}'
)
SYNC_DB_UNKNOWN = '{"database":"Unknown","quota":{"enabled":false,"size":0},"status":"Err","version":"0.21.1"}'


@pytest.mark.parametrize(
    ("service", "status", "body", "database"),
    [
        (SYNC_SERVICE, 200, SYNC_OK, True),
        (SYNC_SERVICE, 200, SYNC_DB_ERR, False),
        (SYNC_SERVICE, 503, SYNC_DB_UNKNOWN, False),
        (SYNC_SERVICE, 503, "not json", False),
        # the accounts' API and profile server: {} and 200, or 503 without their database
        (AUTH_SERVICE, 200, "{}", True),
        (AUTH_SERVICE, 503, '{"code":503,"errno":201,"error":"Service Unavailable"}', False),
        (SERVICES[CONF_PROFILE_URL], 200, "{}", True),
        (SERVICES[CONF_PROFILE_URL], 503, "{}", False),
        # no database of their own
        (SERVICES[CONF_CONTENT_URL], 200, "{}", None),
        (SERVICES[CONF_PAIRING_URL], 200, '{"status":"ok","version":"1.6.0"}', None),
    ],
)
def test_parse_heartbeat(service, status: int, body: str, database: bool | None) -> None:
    """The database's state in each service's heartbeat."""
    assert parse_heartbeat(service, status, body)[0] is database


def test_parse_version() -> None:
    """Dockerflow's version.json; Mozilla's channelserver says TBD."""
    assert (
        parse_version(
            '{"source":"https://github.com/mozilla-services/syncstorage-rs","version":"0.21.1","commit":"a4","build":""}'
        )
        == "0.21.1"
    )
    assert parse_version('{"version":"v1.348.5"}') == "v1.348.5"
    assert parse_version('{"version":"TBD"}') is None
    assert parse_version("<html>") is None
    assert parse_version("[]") is None


def test_http_base() -> None:
    """The pairing server's websocket URL as https."""
    assert http_base("wss://pairing.example.com/") == "https://pairing.example.com"
    assert http_base("ws://pairing:8000") == "http://pairing:8000"
    assert http_base("https://sync.example.com/") == "https://sync.example.com"


async def test_fetch_health_up(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Up, its database Ok, its version from /__version__."""
    mock_service(
        aioclient_mock, SYNC, heartbeat={"database": "Ok", "status": "Ok", "version": "0.21.1"}, version="0.21.1"
    )
    health = await async_fetch_health(async_get_clientsession(hass), SYNC_SERVICE, SYNC)
    assert (health.up, health.database, health.version, health.error) == (True, True, "0.21.1", None)


async def test_fetch_health_database_down(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """The process answers, its database does not; the version from the heartbeat."""
    mock_service(
        aioclient_mock,
        SYNC,
        heartbeat={"database": "Unknown", "status": "Err", "version": "0.21.1"},
        hb_status=503,
        version=None,
    )
    health = await async_fetch_health(async_get_clientsession(hass), SYNC_SERVICE, SYNC)
    assert (health.up, health.database, health.version) == (True, False, "0.21.1")


async def test_fetch_health_no_lbheartbeat(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Without /__lbheartbeat__ the heartbeat's own answer counts."""
    mock_service(aioclient_mock, AUTH, lb_status=404, hb_status=503)
    health = await async_fetch_health(async_get_clientsession(hass), AUTH_SERVICE, AUTH)
    assert (health.up, health.database) == (True, False)


@pytest.mark.parametrize("status", [302, 401, 403, 404, 502])
async def test_fetch_health_blocked(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int) -> None:
    """A sign-in proxy's redirect, a refused client certificate, a dead upstream: down, with the reason."""
    mock_service(aioclient_mock, SYNC, lb_status=status, hb_status=status)
    health = await async_fetch_health(async_get_clientsession(hass), SYNC_SERVICE, SYNC)
    assert health.up is False
    assert health.database is None
    assert str(status) in health.error


async def test_fetch_health_unreachable(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """No connection: down, never an exception."""
    aioclient_mock.get(f"{SYNC}/__lbheartbeat__", exc=aiohttp.ClientConnectionError("refused"))
    health = await async_fetch_health(async_get_clientsession(hass), SYNC_SERVICE, SYNC)
    assert health.up is False
    assert health.error == "refused"


def test_ssl_context_defaults() -> None:
    """Without a CA or a client certificate: the system's CAs, or no check."""
    assert build_ssl_context({}) is True
    assert build_ssl_context({CONF_VERIFY_SSL: True}) is True
    assert build_ssl_context({CONF_VERIFY_SSL: False}) is False


def test_ssl_context_ca_and_client_cert(tls_files: dict[str, str]) -> None:
    """A CA pasted or by path, a client certificate and key pasted."""
    context = build_ssl_context({CONF_CA_CERT: tls_files["ca_pem"]})
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert any(c["subject"] == ((("commonName", "ffs test CA"),),) for c in context.get_ca_certs())

    context = build_ssl_context(
        {
            CONF_CA_CERT: tls_files["ca_path"],
            CONF_CLIENT_CERT: tls_files["cert_pem"],
            CONF_CLIENT_KEY: tls_files["key_pem"],
            CONF_VERIFY_SSL: False,
        }
    )
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode is ssl.CERT_NONE
    assert context.check_hostname is False


@pytest.mark.parametrize(
    ("options", "field"),
    [
        ({CONF_CA_CERT: "/nonexistent/ca.pem"}, CONF_CA_CERT),
        ({CONF_CA_CERT: "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----"}, CONF_CA_CERT),
        ({CONF_CLIENT_CERT: "-----BEGIN CERTIFICATE-----"}, CONF_CLIENT_KEY),
        ({CONF_CLIENT_KEY: "-----BEGIN PRIVATE KEY-----"}, CONF_CLIENT_CERT),
    ],
)
def test_ssl_context_errors(options: dict, field: str) -> None:
    """What does not load names its field."""
    with pytest.raises(TLSConfigError) as err:
        build_ssl_context(options)
    assert err.value.field == field


def test_ssl_context_key_mismatch(tls_material: dict[str, str]) -> None:
    """A key that is not the certificate's."""
    with pytest.raises(TLSConfigError) as err:
        build_ssl_context({CONF_CLIENT_CERT: tls_material["cert_pem"], CONF_CLIENT_KEY: tls_material["cert_pem"]})
    assert err.value.field == CONF_CLIENT_KEY


def test_ssl_context_path_without_pem(tmp_path) -> None:
    """A file that holds no PEM."""
    path = tmp_path / "x.txt"
    path.write_text("hello")
    with pytest.raises(TLSConfigError) as err:
        build_ssl_context({CONF_CA_CERT: str(path)})
    assert "no PEM" in str(err.value)
