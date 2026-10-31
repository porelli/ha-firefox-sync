"""Tests of the config flow: the admin UI, the services' URLs, both, the Supervisor's discovery,
the connection options, reauth and reconfigure."""

from __future__ import annotations

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.firefox_sync.const import (
    CONF_ADDON_SLUG,
    CONF_AUTH_URL,
    CONF_CA_CERT,
    CONF_CLIENT_CERT,
    CONF_CLIENT_KEY,
    CONF_METRICS_TOKEN,
    CONF_PAIRING_URL,
    CONF_SYNC_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
)
from homeassistant import config_entries
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .conftest import AUTH, SYNC, TOKEN, URL, mock_service

DISCOVERY = HassioServiceInfo(
    config={"host": "a0d7b954-firefox-sync", "port": 8080, "metrics_token": TOKEN, "addon": "Firefox Sync"},
    name="Firefox Sync",
    slug="a0d7b954_firefox_sync",
    uuid="1234",
)
DISCOVERED_URL = "http://a0d7b954-firefox-sync:8080"
CONNECTION = {"connection": {CONF_VERIFY_SSL: True}}


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result["flow_id"]


async def test_user_flow_metrics(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, metrics_text: str) -> None:
    """The admin UI's URL (normalized) and token, checked against /metrics with the token."""
    aioclient_mock.get(f"{URL}/metrics", text=metrics_text)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: f" {URL}/ ", CONF_METRICS_TOKEN: f" {TOKEN} ", **CONNECTION}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "ffs-admin.local"
    assert result["data"] == {CONF_URL: URL, CONF_METRICS_TOKEN: TOKEN, CONF_VERIFY_SSL: True}
    # the token went as a bearer token
    assert aioclient_mock.mock_calls[-1][3]["Authorization"] == f"Bearer {TOKEN}"


async def test_user_flow_urls_only(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """No admin UI: the Sync server's URL and the accounts' API URL, checked through their heartbeats."""
    mock_service(aioclient_mock, SYNC, heartbeat={"status": "Ok", "database": "Ok", "version": "0.21.1", "quota": {}})
    mock_service(aioclient_mock, AUTH)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_SYNC_URL: f"{SYNC}/", CONF_AUTH_URL: AUTH, **CONNECTION}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "sync.example.com"
    assert result["data"] == {CONF_SYNC_URL: SYNC, CONF_AUTH_URL: AUTH, CONF_VERIFY_SSL: True}


async def test_user_flow_both(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, metrics_text: str) -> None:
    """The admin UI and the URLs in one entry, named after the admin UI."""
    aioclient_mock.get(f"{URL}/metrics", text=metrics_text)
    mock_service(aioclient_mock, SYNC, heartbeat={"status": "Ok", "database": "Ok"})
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: URL, CONF_METRICS_TOKEN: TOKEN, CONF_SYNC_URL: SYNC, **CONNECTION}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "ffs-admin.local"
    assert result["data"][CONF_SYNC_URL] == SYNC


@pytest.mark.parametrize(
    ("user_input", "errors"),
    [
        ({}, {"base": "no_source"}),
        ({CONF_URL: URL}, {CONF_METRICS_TOKEN: "token_required"}),
        ({CONF_URL: "ftp://x", CONF_METRICS_TOKEN: TOKEN}, {CONF_URL: "invalid_url"}),
        ({CONF_AUTH_URL: AUTH}, {CONF_SYNC_URL: "sync_url_required"}),
        ({CONF_SYNC_URL: "sync.example.com"}, {CONF_SYNC_URL: "invalid_url"}),
        ({CONF_SYNC_URL: SYNC, CONF_PAIRING_URL: "pairing.example.com"}, {CONF_PAIRING_URL: "invalid_url"}),
        ({CONF_URL: "http://a:s@ffs-admin.local:8090", CONF_METRICS_TOKEN: TOKEN}, {CONF_URL: "url_credentials"}),
        ({CONF_SYNC_URL: "https://user@sync.example.com"}, {CONF_SYNC_URL: "url_credentials"}),
        ({CONF_SYNC_URL: SYNC, CONF_AUTH_URL: "https://u:p@api.example.com"}, {CONF_AUTH_URL: "url_credentials"}),
    ],
)
async def test_user_flow_form_errors(hass: HomeAssistant, user_input: dict, errors: dict) -> None:
    """What the form refuses before any request."""
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, {**user_input, **CONNECTION})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors


async def test_user_flow_metrics_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, metrics_text: str
) -> None:
    """A wrong token (404), no answer, a page that is not the admin UI's; then success."""
    flow_id = await _start(hass)
    user = {CONF_URL: URL, CONF_METRICS_TOKEN: "wrong", **CONNECTION}

    aioclient_mock.get(f"{URL}/metrics", status=404)
    result = await hass.config_entries.flow.async_configure(flow_id, user)
    assert result["errors"] == {"base": "invalid_auth"}

    for kwargs in (
        {"exc": aiohttp.ClientConnectionError()},
        {"text": "go_goroutines 12\n"},
        {"text": "<html>x</html>"},
        {"status": 500},
    ):
        aioclient_mock.clear_requests()
        aioclient_mock.get(f"{URL}/metrics", **kwargs)
        result = await hass.config_entries.flow.async_configure(flow_id, user)
        assert result["errors"] == {"base": "cannot_connect"}, kwargs

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/metrics", text=metrics_text)
    result = await hass.config_entries.flow.async_configure(flow_id, {**user, CONF_METRICS_TOKEN: TOKEN})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_service_down(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A URL that is not the service (a sign-in proxy's redirect) is refused, naming it."""
    aioclient_mock.get(f"{SYNC}/__lbheartbeat__", status=302)
    aioclient_mock.get(f"{SYNC}/__heartbeat__", status=302)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_SYNC_URL: SYNC, **CONNECTION})
    assert result["errors"] == {CONF_SYNC_URL: "service_down"}
    assert result["description_placeholders"]["service"] == "Sync server"
    assert "302" in result["description_placeholders"]["error"]


async def test_user_flow_bad_tls(hass: HomeAssistant) -> None:
    """A CA that is no PEM, or a certificate without its key, is refused."""
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {
            CONF_SYNC_URL: SYNC,
            "connection": {
                CONF_VERIFY_SSL: True,
                CONF_CA_CERT: "-----BEGIN CERTIFICATE-----\nnope\n-----END CERTIFICATE-----",
            },
        },
    )
    assert result["errors"] == {"base": "invalid_tls"}
    assert "ca_cert" in result["description_placeholders"]["error"]
    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {CONF_SYNC_URL: SYNC, "connection": {CONF_VERIFY_SSL: True, CONF_CLIENT_CERT: "-----BEGIN CERTIFICATE-----"}},
    )
    assert result["errors"] == {"base": "invalid_tls"}
    assert "client_key" in result["description_placeholders"]["error"]


async def test_user_flow_tls_options_stored(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, tls_files: dict[str, str]
) -> None:
    """The CA (a path) and the client certificate and key (pasted) are kept in the entry."""
    mock_service(aioclient_mock, SYNC, heartbeat={"status": "Ok", "database": "Ok"})
    flow_id = await _start(hass)
    connection = {
        CONF_VERIFY_SSL: True,
        CONF_CA_CERT: tls_files["ca_path"],
        CONF_CLIENT_CERT: tls_files["cert_pem"],
        CONF_CLIENT_KEY: tls_files["key_pem"],
    }
    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_SYNC_URL: SYNC, "connection": connection})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CA_CERT] == tls_files["ca_path"]
    assert result["data"][CONF_CLIENT_KEY] == tls_files["key_pem"].strip()


async def test_user_flow_already_configured(hass: HomeAssistant, mock_entry: MockConfigEntry) -> None:
    """The same admin UI twice is refused."""
    mock_entry.add_to_hass(hass)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: URL, CONF_METRICS_TOKEN: TOKEN, **CONNECTION}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_several_instances(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_entry: MockConfigEntry, metrics_text: str
) -> None:
    """A second admin UI is a second entry."""
    mock_entry.add_to_hass(hass)
    other = "https://admin.example.org"
    aioclient_mock.get(f"{other}/metrics", text=metrics_text)
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: other, CONF_METRICS_TOKEN: TOKEN, **CONNECTION}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "admin.example.org"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 2


async def test_hassio_discovery(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, metrics_text: str) -> None:
    """The add-on's discovery, confirmed by the user."""
    aioclient_mock.get(f"{DISCOVERED_URL}/metrics", text=metrics_text)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HASSIO}, data=DISCOVERY
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "hassio_confirm"
    assert result["description_placeholders"]["addon"] == "Firefox Sync"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Firefox Sync"
    assert result["data"] == {
        CONF_URL: DISCOVERED_URL,
        CONF_METRICS_TOKEN: TOKEN,
        CONF_ADDON_SLUG: "a0d7b954_firefox_sync",
        CONF_VERIFY_SSL: True,
    }
    assert result["result"].unique_id == "a0d7b954_firefox_sync"


async def test_hassio_discovery_cannot_connect(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """The confirmation shows the error when the admin UI does not answer."""
    aioclient_mock.get(f"{DISCOVERED_URL}/metrics", exc=aiohttp.ClientConnectionError())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HASSIO}, data=DISCOVERY
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_hassio_discovery_updates_entry(hass: HomeAssistant) -> None:
    """A new discovery of the same add-on (a new token or host) updates its entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="a0d7b954_firefox_sync",
        data={CONF_URL: "http://old:8080", CONF_METRICS_TOKEN: "old", CONF_ADDON_SLUG: "a0d7b954_firefox_sync"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HASSIO}, data=DISCOVERY
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_URL] == DISCOVERED_URL
    assert entry.data[CONF_METRICS_TOKEN] == TOKEN


async def test_hassio_discovery_invalid(hass: HomeAssistant) -> None:
    """A discovery without host, port or token is refused."""
    bad = HassioServiceInfo(config={"host": "x"}, name="Firefox Sync", slug="local_firefox_sync", uuid="1")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_HASSIO}, data=bad
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_discovery_info"


async def test_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_entry: MockConfigEntry, metrics_text: str
) -> None:
    """A new token, checked, then the entry reloaded."""
    mock_entry.add_to_hass(hass)
    result = await mock_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    aioclient_mock.get(f"{URL}/metrics", status=404)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_METRICS_TOKEN: "still-wrong"})
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/metrics", text=metrics_text)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_METRICS_TOKEN: "new-token"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_entry.data[CONF_METRICS_TOKEN] == "new-token"


async def test_reconfigure(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_entry: MockConfigEntry, metrics_text: str
) -> None:
    """New URLs and options for an entry, no new entry; adding the Sync server's URL to it."""
    mock_entry.add_to_hass(hass)
    new = "http://192.168.1.10:8090"
    result = await mock_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "nope", CONF_METRICS_TOKEN: TOKEN, **CONNECTION}
    )
    assert result["errors"] == {CONF_URL: "invalid_url"}

    aioclient_mock.get(f"{new}/metrics", text=metrics_text)
    mock_service(aioclient_mock, SYNC, heartbeat={"status": "Ok", "database": "Ok"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_URL: new, CONF_METRICS_TOKEN: "t2", CONF_SYNC_URL: SYNC, "connection": {CONF_VERIFY_SSL: False}},
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_entry.data == {CONF_URL: new, CONF_METRICS_TOKEN: "t2", CONF_SYNC_URL: SYNC, CONF_VERIFY_SSL: False}
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
