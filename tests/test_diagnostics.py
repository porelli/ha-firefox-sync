"""Tests of the diagnostics."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.firefox_sync.const import (
    CONF_CA_CERT,
    CONF_CLIENT_CERT,
    CONF_CLIENT_KEY,
    CONF_SYNC_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
)
from homeassistant.core import HomeAssistant

from .conftest import SYNC, TOKEN, URL, mock_service


async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    mock_entry: MockConfigEntry,
    metrics_text: str,
) -> None:
    """The token is redacted, the metrics are there but the statsd counters."""
    aioclient_mock.get(f"{URL}/metrics", text=metrics_text)
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, mock_entry)
    assert TOKEN not in str(diagnostics)
    assert diagnostics["entry"]["data"] == {"url": URL, "metrics_token": "**REDACTED**"}
    assert diagnostics["services"] == {}
    assert diagnostics["last_update_success"] is True
    assert diagnostics["metrics"]["ffs_sync_accounts"] == [{"labels": {}, "value": 3.0}]
    assert {"labels": {"component": "Storage database"}, "value": 0.0} in diagnostics["metrics"]["ffs_up"]
    assert not any(name.startswith("ffs_statsd_") for name in diagnostics["metrics"])


async def test_diagnostics_redacts_tls_and_shows_services(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    tls_material: dict[str, str],
) -> None:
    """The client key and certificates are redacted; the services' heartbeats are there."""
    mock_service(aioclient_mock, SYNC, heartbeat={"status": "Ok", "database": "Ok", "version": "0.21.1"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="sync.example.com",
        data={
            CONF_SYNC_URL: SYNC,
            CONF_VERIFY_SSL: True,
            CONF_CA_CERT: tls_material["ca_pem"],
            CONF_CLIENT_CERT: tls_material["cert_pem"],
            CONF_CLIENT_KEY: tls_material["key_pem"],
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    text = str(diagnostics)
    assert "PRIVATE KEY" not in text
    assert "BEGIN CERTIFICATE" not in text
    assert diagnostics["entry"]["data"][CONF_CLIENT_KEY] == "**REDACTED**"
    assert diagnostics["services"]["Sync server"]["up"] is True
    assert diagnostics["services"]["Sync server"]["database"] is True
    assert diagnostics["services"]["Sync server"]["version"] == "1.2.3"
