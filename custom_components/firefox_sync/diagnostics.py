"""Diagnostics of the Firefox Sync integration: the entry without its secrets, the last round."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_CA_CERT, CONF_CLIENT_CERT, CONF_CLIENT_KEY, CONF_METRICS_TOKEN
from .coordinator import FirefoxSyncConfigEntry

# the token, the client key and the certificates (which name their holder)
TO_REDACT = {CONF_METRICS_TOKEN, CONF_CLIENT_KEY, CONF_CLIENT_CERT, CONF_CA_CERT}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: FirefoxSyncConfigEntry) -> dict[str, Any]:
    """The entry (secrets redacted), the merged metrics and the services' health."""
    coordinator = entry.runtime_data
    data = coordinator.data
    result: dict[str, Any] = {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "source": entry.source,
        },
        "last_update_success": coordinator.last_update_success,
        "metrics": None,
        "services": None,
    }
    if data is None:
        return result
    result["metrics_error"] = data.metrics_error
    result["metrics"] = {
        name: [{"labels": dict(sample.labels), "value": sample.value} for sample in samples]
        for name, samples in sorted(data.metrics.samples.items())
        # the Sync server's statsd counters are many and say little here
        if not name.startswith("ffs_statsd_")
    }
    result["services"] = {
        component: {
            "up": health.up,
            "database": health.database,
            "version": health.version,
            "error": health.error,
            "heartbeat": health.details,
        }
        for component, health in data.health.items()
    }
    return result
