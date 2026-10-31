"""The coordinator of the Firefox Sync integration: one round of requests per poll.

A round is the admin UI's /metrics (when the entry has its URL and token) and the
Dockerflow endpoints of each public URL of the entry. The entities read one set of
metrics: the admin UI's, with the services' health merged in as the admin UI names it
(ffs_up{component}), so that an entry with both has one entity per component.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import ssl

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    SERVICES,
    MetricsAuthError,
    MetricsConnectionError,
    ServiceHealth,
    TLSConfigError,
    async_fetch_health,
    async_fetch_metrics,
    build_ssl_context,
)
from .const import CONF_METRICS_TOKEN, DOMAIN, LOGGER, SCAN_INTERVAL, SERVICE_URLS
from .prometheus import Metrics, Sample

type FirefoxSyncConfigEntry = ConfigEntry[FirefoxSyncCoordinator]


@dataclass
class FirefoxSyncData:
    """One round: the merged metrics, the versions, and whether the admin UI answered."""

    metrics: Metrics
    versions: dict[str, str] = field(default_factory=dict)
    health: dict[str, ServiceHealth] = field(default_factory=dict)
    # the entry has the admin UI, which did not answer this round (the services' URLs did)
    metrics_error: str | None = None


def merge(metrics: Metrics | None, health: list[ServiceHealth]) -> Metrics:
    """The admin UI's metrics with the services' health as ffs_up{component}.

    Each service's state as seen from Home Assistant replaces the admin UI's sample of
    the same component. The state of a service's database is added only without the
    admin UI's metrics, which have each database on its own (tokenserver, storage,
    accounts).
    """
    merged = Metrics()
    if metrics is not None:
        merged.types = dict(metrics.types)
        merged.samples = {name: list(samples) for name, samples in metrics.samples.items()}
    replaced = {h.service.component for h in health}
    up = [s for s in merged.samples.get("ffs_up", []) if s.label("component") not in replaced]
    for item in health:
        up.append(Sample("ffs_up", (("component", item.service.component),), 1.0 if item.up else 0.0))
        if metrics is None and item.service.database is not None and item.database is not None:
            up.append(Sample("ffs_up", (("component", item.service.database),), 1.0 if item.database else 0.0))
    if up:
        merged.samples["ffs_up"] = up
    return merged


class FirefoxSyncCoordinator(DataUpdateCoordinator[FirefoxSyncData]):
    """Polls the admin UI's /metrics and the services' public URLs."""

    config_entry: FirefoxSyncConfigEntry

    def __init__(self, hass: HomeAssistant, config_entry: FirefoxSyncConfigEntry) -> None:
        """Set up the coordinator of one entry."""
        super().__init__(
            hass,
            LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=SCAN_INTERVAL,
        )
        self.session = async_get_clientsession(hass)
        self.ssl_context: ssl.SSLContext | bool = True

    @property
    def has_metrics(self) -> bool:
        """Whether the entry has the admin UI's metrics."""
        return bool(self.config_entry.data.get(CONF_URL))

    async def _async_setup(self) -> None:
        """Load the TLS options once."""
        try:
            self.ssl_context = await self.hass.async_add_executor_job(build_ssl_context, self.config_entry.data)
        except TLSConfigError as err:
            raise ConfigEntryError(
                translation_domain=DOMAIN, translation_key="tls_config", translation_placeholders={"error": str(err)}
            ) from err

    async def _async_update_data(self) -> FirefoxSyncData:
        """One round of requests."""
        data = self.config_entry.data
        urls = [(SERVICES[key], data[key]) for key in SERVICE_URLS if data.get(key)]
        health_task = asyncio.gather(
            *(async_fetch_health(self.session, service, url, self.ssl_context) for service, url in urls)
        )
        metrics: Metrics | None = None
        metrics_error: str | None = None
        try:
            if self.has_metrics:
                try:
                    metrics = await async_fetch_metrics(
                        self.session, data[CONF_URL], data[CONF_METRICS_TOKEN], self.ssl_context
                    )
                except MetricsConnectionError as err:
                    # with the services' URLs their health still counts; without, nothing does
                    if not urls:
                        raise UpdateFailed(
                            translation_domain=DOMAIN,
                            translation_key="cannot_connect",
                            translation_placeholders={"error": str(err)},
                        ) from err
                    metrics_error = str(err)
                    LOGGER.debug("The admin UI did not answer: %s", err)
        except MetricsAuthError as err:
            health_task.cancel()
            raise ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="invalid_auth") from err
        except BaseException:
            health_task.cancel()
            raise
        health = list(await health_task)
        return FirefoxSyncData(
            metrics=merge(metrics, health),
            versions={h.service.component: h.version for h in health if h.version},
            health={h.service.component: h for h in health},
            metrics_error=metrics_error,
        )
