"""Tests of the coordinator and the entities it feeds."""

from __future__ import annotations

from datetime import timedelta

import aiohttp
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.firefox_sync.const import (
    CONF_ADDON_SLUG,
    CONF_AUTH_URL,
    CONF_CA_CERT,
    CONF_CONTENT_URL,
    CONF_METRICS_TOKEN,
    CONF_PAIRING_URL,
    CONF_PROFILE_URL,
    CONF_SYNC_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_URL, STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .conftest import AUTH, CONTENT, PAIRING, PROFILE, SYNC, URL, fixture, mock_service


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _entity_id(hass: HomeAssistant, entry: MockConfigEntry, platform: str, unique: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{unique}")
    assert entity_id, unique
    return entity_id


async def test_entities(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_entry: MockConfigEntry, metrics_text: str
) -> None:
    """Every metric of the scrape, with its unit, device class and state class, on one device."""
    aioclient_mock.get(f"{URL}/metrics", text=metrics_text)
    await _setup(hass, mock_entry)
    assert mock_entry.state is ConfigEntryState.LOADED

    def state(platform: str, unique: str):
        return hass.states.get(_entity_id(hass, mock_entry, platform, unique))

    accounts = state("sensor", "sync_accounts")
    assert accounts.state == "3"
    assert accounts.attributes["state_class"] == "measurement"
    assert accounts.attributes["friendly_name"] == "ffs-admin.local Sync accounts"
    assert state("sensor", "sync_max_users").state == "5"
    assert state("sensor", "sync_stored_items").state == "1234"

    stored = state("sensor", "sync_stored_bytes")
    assert stored.attributes["device_class"] == "data_size"
    assert stored.attributes["unit_of_measurement"] == "MiB"
    assert float(stored.state) == 5.0

    db = state("sensor", "database_bytes_syncstorage")
    assert float(db.state) == 10.0
    assert db.attributes["friendly_name"] == "ffs-admin.local Database syncstorage"

    backup = state("sensor", "backup_age_sync")
    assert backup.attributes["device_class"] == "duration"
    assert backup.attributes["unit_of_measurement"] == "h"
    assert float(backup.state) == 2.0

    ratio = state("sensor", "sync_error_ratio")
    assert ratio.attributes["unit_of_measurement"] == "%"
    assert float(ratio.state) == 2.5

    cert = state("sensor", "cert_expiry_sync.example.com")
    assert cert.attributes["unit_of_measurement"] == "d"
    assert float(cert.state) == 60

    failures = state("sensor", "notify_failures_ntfy")
    assert failures.state == "2"
    assert failures.attributes["state_class"] == "total_increasing"

    up = state("binary_sensor", "component_up_Sync server")
    assert up.state == STATE_ON
    assert up.attributes["device_class"] == "connectivity"
    assert up.attributes["friendly_name"] == "ffs-admin.local Sync server"
    assert state("binary_sensor", "component_up_Storage database").state == STATE_OFF
    assert state("binary_sensor", "sync_full").state == STATE_OFF
    assert state("binary_sensor", "sync_near_max_users").attributes["device_class"] == "problem"
    assert state("binary_sensor", "backup_failed_sync").state == STATE_OFF
    assert state("binary_sensor", "sync_error_spike").state == STATE_OFF
    assert state("binary_sensor", "notify_failing_ntfy").state == STATE_ON

    # the accounts' metrics are not in this scrape: no such entities
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{mock_entry.entry_id}_accounts") is None
    assert registry.async_get_entity_id("binary_sensor", DOMAIN, f"{mock_entry.entry_id}_accounts_full") is None

    # one device, a service
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), mock_entry.entry_id)
    assert len(devices) == 1
    assert devices[0].entry_type is dr.DeviceEntryType.SERVICE
    assert devices[0].configuration_url == URL
    entities = er.async_entries_for_config_entry(registry, mock_entry.entry_id)
    assert {e.device_id for e in entities} == {devices[0].id}


async def test_new_metrics_add_entities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_entry: MockConfigEntry,
    metrics_text: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Metrics that appear later (the accounts, a new component) get their entities; one that goes is unknown."""
    aioclient_mock.get(f"{URL}/metrics", text=metrics_text)
    await _setup(hass, mock_entry)

    later = metrics_text.replace("ffs_sync_max_users 5\n", "") + fixture("metrics_accounts.txt")
    later += 'ffs_up{component="Accounts"} 1\n'
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/metrics", text=later)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get(_entity_id(hass, mock_entry, "sensor", "accounts")).state == "2"
    assert hass.states.get(_entity_id(hass, mock_entry, "sensor", "accounts_max")).state == "10"
    assert hass.states.get(_entity_id(hass, mock_entry, "binary_sensor", "accounts_full")).state == STATE_OFF
    assert hass.states.get(_entity_id(hass, mock_entry, "binary_sensor", "component_up_Accounts")).state == STATE_ON
    # SYNC_MAX_USERS=unlimited: no ffs_sync_max_users
    assert hass.states.get(_entity_id(hass, mock_entry, "sensor", "sync_max_users")).state == STATE_UNKNOWN


async def test_unavailable_then_back(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_entry: MockConfigEntry,
    metrics_text: str,
    freezer: FrozenDateTimeFactory,
) -> None:
    """The admin UI gone: every entity unavailable, until it answers again."""
    aioclient_mock.get(f"{URL}/metrics", text=metrics_text)
    await _setup(hass, mock_entry)
    entity_id = _entity_id(hass, mock_entry, "sensor", "sync_accounts")

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/metrics", exc=aiohttp.ClientConnectionError())
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/metrics", text=metrics_text)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(entity_id).state == "3"


async def test_setup_retries_when_unreachable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_entry: MockConfigEntry
) -> None:
    """Not reachable at the start: setup is retried later."""
    aioclient_mock.get(f"{URL}/metrics", exc=aiohttp.ClientConnectionError())
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_entry.state is ConfigEntryState.SETUP_RETRY


async def test_wrong_token_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_entry: MockConfigEntry
) -> None:
    """The admin UI answers 404 to a wrong token: setup fails and asks for a new one."""
    aioclient_mock.get(f"{URL}/metrics", status=404)
    mock_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_unload(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_entry: MockConfigEntry, metrics_text: str
) -> None:
    """The entry unloads."""
    aioclient_mock.get(f"{URL}/metrics", text=metrics_text)
    await _setup(hass, mock_entry)
    assert await hass.config_entries.async_unload(mock_entry.entry_id)
    assert mock_entry.state is ConfigEntryState.NOT_LOADED


async def test_addon_device_links_to_ingress(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, metrics_text: str
) -> None:
    """An entry of the add-on: its device links to the add-on's ingress panel."""
    url = "http://a0d7b954-firefox-sync:8080"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Firefox Sync",
        unique_id="a0d7b954_firefox_sync",
        data={CONF_URL: url, CONF_METRICS_TOKEN: "t", CONF_ADDON_SLUG: "a0d7b954_firefox_sync"},
    )
    aioclient_mock.get(f"{url}/metrics", text=metrics_text)
    await _setup(hass, entry)
    device = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)[0]
    assert device.configuration_url == "homeassistant://hassio/ingress/a0d7b954_firefox_sync"


async def test_urls_only(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """No admin UI: one connectivity sensor per service, its database's where it has one, versions."""
    mock_service(
        aioclient_mock, SYNC, heartbeat={"database": "Ok", "status": "Ok", "version": "0.21.1"}, version="0.21.1"
    )
    mock_service(aioclient_mock, AUTH, version="v1.348.5")
    mock_service(aioclient_mock, CONTENT, version="v1.348.5")
    mock_service(aioclient_mock, PROFILE, hb_status=503, version="v1.348.5")
    mock_service(
        aioclient_mock, "https://pairing.example.com", heartbeat={"status": "ok", "version": "1.6.0"}, version="TBD"
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="sync.example.com",
        data={
            CONF_SYNC_URL: SYNC,
            CONF_AUTH_URL: AUTH,
            CONF_CONTENT_URL: CONTENT,
            CONF_PROFILE_URL: PROFILE,
            CONF_PAIRING_URL: PAIRING,
            CONF_VERIFY_SSL: True,
        },
    )
    await _setup(hass, entry)

    def state(platform: str, unique: str):
        return hass.states.get(_entity_id(hass, entry, platform, unique))

    sync = state("binary_sensor", "component_up_Sync server")
    assert sync.state == STATE_ON
    assert sync.attributes["device_class"] == "connectivity"
    assert state("binary_sensor", "component_up_Sync server database").state == STATE_ON
    assert state("binary_sensor", "component_up_Accounts API").state == STATE_ON
    assert state("binary_sensor", "component_up_Accounts database").state == STATE_ON
    assert state("binary_sensor", "component_up_Accounts pages").state == STATE_ON
    assert state("binary_sensor", "component_up_Profile server").state == STATE_ON
    assert state("binary_sensor", "component_up_Profile server database").state == STATE_OFF
    assert state("binary_sensor", "component_up_Pairing server").state == STATE_ON
    version = state("sensor", "version_Sync server")
    assert version.state == "0.21.1"
    assert version.attributes["friendly_name"] == "sync.example.com Sync server version"
    assert state("sensor", "version_Accounts API").state == "v1.348.5"
    # the channelserver's TBD: its heartbeat's version
    assert state("sensor", "version_Pairing server").state == "1.6.0"
    # no metrics: none of the admin UI's entities
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_sync_accounts") is None

    device = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)[0]
    assert device.sw_version == "0.21.1"
    # no deprecated helper of Home Assistant (its frame reports are logged, not raised)
    assert "Detected that custom integration" not in caplog.text
    assert device.configuration_url == SYNC

    # the Sync server goes down: off, its database unknown; not unavailable
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{SYNC}/__lbheartbeat__", exc=aiohttp.ClientConnectionError())
    for base in (AUTH, CONTENT, PROFILE, "https://pairing.example.com"):
        mock_service(aioclient_mock, base)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert state("binary_sensor", "component_up_Sync server").state == STATE_OFF
    assert state("binary_sensor", "component_up_Sync server database").state == STATE_UNKNOWN


async def test_metrics_and_urls_no_duplicates(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, metrics_text: str, freezer: FrozenDateTimeFactory
) -> None:
    """Both in one entry: one entity per component (the URL's view wins), the databases from the metrics."""
    aioclient_mock.get(f"{URL}/metrics", text=metrics_text)
    mock_service(aioclient_mock, SYNC, heartbeat={"database": "Err", "status": "Err"}, lb_status=200)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ffs-admin.local",
        data={CONF_URL: URL, CONF_METRICS_TOKEN: "t", CONF_SYNC_URL: SYNC, CONF_VERIFY_SSL: True},
    )
    await _setup(hass, entry)
    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    unique_ids = [e.unique_id for e in entities]
    assert len(unique_ids) == len(set(unique_ids))
    sync_up = [u for u in unique_ids if u.endswith("_component_up_Sync server")]
    assert len(sync_up) == 1
    # the heartbeat's database is left to the metrics' own databases
    assert not any(u.endswith("_component_up_Sync server database") for u in unique_ids)
    assert hass.states.get(_entity_id(hass, entry, "binary_sensor", "component_up_Storage database")).state == STATE_OFF
    assert hass.states.get(_entity_id(hass, entry, "binary_sensor", "component_up_Sync server")).state == STATE_ON
    assert hass.states.get(_entity_id(hass, entry, "sensor", "sync_accounts")).state == "3"

    # the admin UI gone, the Sync server still there: the admin UI's entities unavailable only
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/metrics", exc=aiohttp.ClientConnectionError())
    mock_service(aioclient_mock, SYNC, heartbeat={"database": "Ok", "status": "Ok"})
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get(_entity_id(hass, entry, "sensor", "sync_accounts")).state == STATE_UNAVAILABLE
    assert (
        hass.states.get(_entity_id(hass, entry, "binary_sensor", "component_up_Storage database")).state
        == STATE_UNAVAILABLE
    )
    assert hass.states.get(_entity_id(hass, entry, "binary_sensor", "component_up_Sync server")).state == STATE_ON
    assert hass.states.get(_entity_id(hass, entry, "sensor", "version_Sync server")).state == "1.2.3"


async def test_bad_tls_options_fail_setup(hass: HomeAssistant) -> None:
    """A stored CA that no longer loads (a path gone): the entry fails setup, named."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_SYNC_URL: SYNC, CONF_CA_CERT: "/nonexistent/ca.pem"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
