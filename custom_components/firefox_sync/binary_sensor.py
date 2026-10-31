"""The binary sensors of the Firefox Sync integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FirefoxSyncConfigEntry, FirefoxSyncCoordinator
from .entity import FirefoxSyncEntity, FirefoxSyncEntityDescription, async_track_new_entities

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class FirefoxSyncBinarySensorDescription(FirefoxSyncEntityDescription, BinarySensorEntityDescription):
    """A binary sensor of a 0/1 metric; is_on_fn turns the value."""

    is_on_fn: Callable[[float], bool] = lambda value: value == 1


BINARY_SENSORS: tuple[FirefoxSyncBinarySensorDescription, ...] = (
    FirefoxSyncBinarySensorDescription(
        key="component_up",
        translation_key="component_up",
        metric="ffs_up",
        label="component",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
    ),
    FirefoxSyncBinarySensorDescription(
        key="sync_near_max_users",
        translation_key="sync_near_max_users",
        metric="ffs_sync_near_max_users",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
    FirefoxSyncBinarySensorDescription(
        key="sync_full",
        translation_key="sync_full",
        metric="ffs_sync_full",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
    FirefoxSyncBinarySensorDescription(
        key="accounts_near_max",
        translation_key="accounts_near_max",
        metric="ffs_accounts_near_max",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
    FirefoxSyncBinarySensorDescription(
        key="accounts_full",
        translation_key="accounts_full",
        metric="ffs_accounts_full",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
    FirefoxSyncBinarySensorDescription(
        # ffs_backup_last_ok is 1 when the last run worked: on (a problem) when it did not
        key="backup_failed",
        translation_key="backup_failed",
        metric="ffs_backup_last_ok",
        label="scope",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda value: value != 1,
    ),
    FirefoxSyncBinarySensorDescription(
        key="sync_error_spike",
        translation_key="sync_error_spike",
        metric="ffs_sync_error_spike",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
    FirefoxSyncBinarySensorDescription(
        key="notify_failing",
        translation_key="notify_failing",
        metric="ffs_notify_send_failing",
        label="destination",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FirefoxSyncBinarySensorDescription(
        key="alert_email_failing",
        translation_key="alert_email_failing",
        metric="ffs_alert_send_failing",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FirefoxSyncConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors of one admin UI."""
    async_track_new_entities(entry, BINARY_SENSORS, FirefoxSyncBinarySensor, async_add_entities)


class FirefoxSyncBinarySensor(FirefoxSyncEntity, BinarySensorEntity):
    """A binary sensor of a metric."""

    entity_description: FirefoxSyncBinarySensorDescription

    def __init__(
        self,
        coordinator: FirefoxSyncCoordinator,
        description: FirefoxSyncBinarySensorDescription,
        label_value: str | None = None,
    ) -> None:
        """Set up the binary sensor."""
        super().__init__(coordinator, description, label_value)

    @property
    def is_on(self) -> bool | None:
        """The state, None (unknown) when the last scrape had no such metric."""
        value = self.metric_value
        if value is None:
            return None
        return self.entity_description.is_on_fn(value)
