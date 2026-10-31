"""The sensors of the Firefox Sync integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FirefoxSyncConfigEntry, FirefoxSyncCoordinator
from .entity import FirefoxSyncEntity, FirefoxSyncEntityDescription, async_track_new_entities

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class FirefoxSyncSensorDescription(FirefoxSyncEntityDescription, SensorEntityDescription):
    """A sensor of a metric, its value turned by value_fn."""

    value_fn: Callable[[float], float] = lambda value: value


def _count(value: float) -> float:
    return int(value)


SENSORS: tuple[FirefoxSyncSensorDescription, ...] = (
    FirefoxSyncSensorDescription(
        key="sync_accounts",
        translation_key="sync_accounts",
        metric="ffs_sync_accounts",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_count,
    ),
    FirefoxSyncSensorDescription(
        key="sync_max_users",
        translation_key="sync_max_users",
        metric="ffs_sync_max_users",
        value_fn=_count,
    ),
    FirefoxSyncSensorDescription(
        key="sync_stored_items",
        translation_key="sync_stored_items",
        metric="ffs_sync_stored_items",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_count,
    ),
    FirefoxSyncSensorDescription(
        key="sync_stored_bytes",
        translation_key="sync_stored_bytes",
        metric="ffs_sync_stored_bytes",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.MEBIBYTES,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    FirefoxSyncSensorDescription(
        key="database_bytes",
        translation_key="database_bytes",
        metric="ffs_database_bytes",
        label="database",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        suggested_unit_of_measurement=UnitOfInformation.MEBIBYTES,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    FirefoxSyncSensorDescription(
        key="backup_age",
        translation_key="backup_age",
        metric="ffs_backup_age_seconds",
        label="scope",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    FirefoxSyncSensorDescription(
        key="sync_error_ratio",
        translation_key="sync_error_ratio",
        metric="ffs_sync_error_ratio",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda ratio: ratio * 100,
    ),
    FirefoxSyncSensorDescription(
        key="cert_expiry",
        translation_key="cert_expiry",
        metric="ffs_cert_expiry_seconds",
        label="host",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.DAYS,
        suggested_display_precision=0,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FirefoxSyncSensorDescription(
        key="accounts",
        translation_key="accounts",
        metric="ffs_accounts",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_count,
    ),
    FirefoxSyncSensorDescription(
        key="accounts_max",
        translation_key="accounts_max",
        metric="ffs_accounts_max",
        value_fn=_count,
    ),
    FirefoxSyncSensorDescription(
        key="version",
        translation_key="version",
        metric="__version__",
        label="component",
        versions=True,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FirefoxSyncSensorDescription(
        key="notify_failures",
        translation_key="notify_failures",
        metric="ffs_notify_failures_total",
        label="destination",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_count,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FirefoxSyncConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors of one admin UI."""
    async_track_new_entities(entry, SENSORS, FirefoxSyncSensor, async_add_entities)


class FirefoxSyncSensor(FirefoxSyncEntity, SensorEntity):
    """A sensor of a metric."""

    entity_description: FirefoxSyncSensorDescription

    def __init__(
        self,
        coordinator: FirefoxSyncCoordinator,
        description: FirefoxSyncSensorDescription,
        label_value: str | None = None,
    ) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, description, label_value)

    @property
    def native_value(self) -> float | str | None:
        """The value of the metric, None (unknown) when the last scrape had none."""
        if self.entity_description.versions:
            return self.coordinator.data.versions.get(self.label_value or "")
        value = self.metric_value
        if value is None:
            return None
        return self.entity_description.value_fn(value)
