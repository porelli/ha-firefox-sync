"""The base entity of the Firefox Sync integration: one metric, or one label value of it."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from homeassistant.const import CONF_URL
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import SERVICES
from .const import CONF_ADDON_SLUG, CONF_SYNC_URL, DOMAIN
from .coordinator import FirefoxSyncConfigEntry, FirefoxSyncCoordinator


@dataclass(frozen=True, kw_only=True)
class FirefoxSyncEntityDescription(EntityDescription):
    """An entity of one metric of the admin UI; with label, one entity per value of it."""

    metric: str
    label: str | None = None
    # one entity per service with a version (/__version__ of its public URL), label "component"
    versions: bool = False


def device_info(entry: FirefoxSyncConfigEntry) -> DeviceInfo:
    """One device (a service) per entry: its admin UI, or its Sync server when it has only the public URLs."""
    if slug := entry.data.get(CONF_ADDON_SLUG):
        # the add-on's ingress panel, where its admin UI is
        url = f"homeassistant://hassio/ingress/{slug}"
    else:
        url = entry.data.get(CONF_URL) or entry.data.get(CONF_SYNC_URL)
    version = None
    if (data := entry.runtime_data.data) is not None:
        version = data.versions.get(SERVICES[CONF_SYNC_URL].component)
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="firefox-sync",
        model="Firefox Sync admin UI" if CONF_URL in entry.data else "Firefox Sync server",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=url,
        sw_version=version,
    )


class FirefoxSyncEntity(CoordinatorEntity[FirefoxSyncCoordinator]):
    """A metric of the admin UI."""

    _attr_has_entity_name = True
    entity_description: FirefoxSyncEntityDescription

    def __init__(
        self,
        coordinator: FirefoxSyncCoordinator,
        description: FirefoxSyncEntityDescription,
        label_value: str | None = None,
    ) -> None:
        """Set up the entity of a metric, or of one value of its label."""
        super().__init__(coordinator)
        self.entity_description = description
        self.label_value = label_value
        entry = coordinator.config_entry
        unique = f"{entry.entry_id}_{description.key}"
        if description.label is not None and label_value is not None:
            unique += f"_{label_value}"
            self._attr_translation_placeholders = {description.label: label_value}
        self._attr_unique_id = unique
        self._attr_device_info = device_info(entry)

    @property
    def metric_value(self) -> float | None:
        """The value of the metric in the last scrape, None when it is not there."""
        labels = {}
        if self.entity_description.label is not None and self.label_value is not None:
            labels[self.entity_description.label] = self.label_value
        return self.coordinator.data.metrics.get(self.entity_description.metric, **labels)

    @property
    def available(self) -> bool:
        """Unavailable when its source did not answer.

        With both the admin UI and the services' URLs, a round where only the URLs
        answered leaves the admin UI's own metrics without a value: those are unavailable.
        """
        if not super().available:
            return False
        if self.entity_description.versions:
            return True
        return self.coordinator.data.metrics_error is None or self.metric_value is not None


def new_entities[D: FirefoxSyncEntityDescription](
    coordinator: FirefoxSyncCoordinator,
    descriptions: Iterable[D],
    known: set[tuple[str, str | None]],
    factory: Callable[[FirefoxSyncCoordinator, D, str | None], FirefoxSyncEntity],
) -> list[FirefoxSyncEntity]:
    """The entities of the metrics (and label values) in the last scrape not yet added.

    Metrics come and go with the setup of the stack: the accounts' only with the
    self-hosted accounts, max users only with a limit, a backup scope after its first run.
    """
    data = coordinator.data.metrics
    entities: list[FirefoxSyncEntity] = []
    for description in descriptions:
        values: list[str | None]
        if description.versions:
            values = list(coordinator.data.versions)
        elif description.metric not in data:
            continue
        elif description.label is None:
            values = [None]
        else:
            values = list(data.label_values(description.metric, description.label))
        for value in values:
            key = (description.key, value)
            if key in known:
                continue
            known.add(key)
            entities.append(factory(coordinator, description, value))
    return entities


@callback
def async_track_new_entities[D: FirefoxSyncEntityDescription](
    entry: FirefoxSyncConfigEntry,
    descriptions: tuple[D, ...],
    factory: Callable[[FirefoxSyncCoordinator, D, str | None], FirefoxSyncEntity],
    add_entities: Callable[[list[FirefoxSyncEntity]], None],
) -> None:
    """Add the entities of the metrics there now, and of those that appear later."""
    coordinator = entry.runtime_data
    known: set[tuple[str, str | None]] = set()

    @callback
    def _add_new() -> None:
        if entities := new_entities(coordinator, descriptions, known, factory):
            add_entities(entities)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))
