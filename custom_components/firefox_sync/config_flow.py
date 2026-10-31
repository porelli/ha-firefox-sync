"""The config flow of the Firefox Sync integration.

By hand, one form with two sources, either or both:
- the admin UI's URL and its metrics token (ADMIN_METRICS_TOKEN of firefox-sync), checked by
  fetching /metrics: every entity;
- the public URLs of the services, without the admin UI: the Sync server's (required then),
  and the self-hosted accounts' API, pages, profile and pairing servers (optional), checked
  through their Dockerflow endpoints (/__lbheartbeat__, /__heartbeat__, /__version__);
and the connection options of both: TLS verification, a custom CA, a client certificate.

Or the Supervisor's discovery, sent by the Firefox Sync add-on with the admin UI's address
on the add-on network and the token.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from yarl import URL

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_URL
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .api import (
    SERVICES,
    MetricsAuthError,
    MetricsConnectionError,
    TLSConfigError,
    async_fetch_health,
    async_fetch_metrics,
    build_ssl_context,
)
from .const import (
    CONF_ADDON_SLUG,
    CONF_CA_CERT,
    CONF_CLIENT_CERT,
    CONF_CLIENT_KEY,
    CONF_METRICS_TOKEN,
    CONF_PAIRING_URL,
    CONF_SYNC_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
    LOGGER,
    SERVICE_URLS,
)

try:  # Home Assistant 2026.9 and later validate with Probatio
    import probatio as vol
except ImportError:  # pragma: no cover - older Home Assistant
    import voluptuous as vol  # type: ignore[no-redef]

CONNECTION = "connection"
_URL = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))
_TEXT = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT))
_SECRET = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
_PEM = TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True))


def _form_schema() -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Optional(CONF_URL): _URL,
        vol.Optional(CONF_METRICS_TOKEN): _SECRET,
    }
    for key in SERVICE_URLS:
        # the pairing server's URL is a wss:// one: no URL selector
        fields[vol.Optional(key)] = _TEXT if key == CONF_PAIRING_URL else _URL
    fields[vol.Required(CONNECTION)] = section(
        vol.Schema(
            {
                vol.Required(CONF_VERIFY_SSL, default=True): BooleanSelector(),
                vol.Optional(CONF_CA_CERT): _PEM,
                vol.Optional(CONF_CLIENT_CERT): _PEM,
                vol.Optional(CONF_CLIENT_KEY): _PEM,
            }
        ),
        {"collapsed": True},
    )
    return vol.Schema(fields)


def _has_credentials(url: str) -> bool:
    """Whether the URL carries a user or password (https://user:pass@host): refused, they would end
    up in diagnostics, errors and the device's link; the integration authenticates with the
    metrics token or a client certificate, never with a password in the URL."""
    try:
        parsed = URL(url.strip())
    except ValueError:
        return False
    return bool(parsed.user or parsed.password)


def _normalize_url(url: str, *, websocket: bool = False) -> str | None:
    """The base URL without a trailing slash, None when it is not http(s) (or ws(s))."""
    try:
        parsed = URL(url.strip())
    except ValueError:
        return None
    schemes = ("http", "https", "ws", "wss") if websocket else ("http", "https")
    if parsed.scheme not in schemes or not parsed.host:
        return None
    return str(parsed.with_query(None).with_fragment(None)).rstrip("/")


def _suggested(data: Mapping[str, Any]) -> dict[str, Any]:
    """An entry's data as the form's suggested values."""
    values = {key: value for key, value in data.items() if key in (CONF_URL, CONF_METRICS_TOKEN, *SERVICE_URLS)}
    # (the form's own input has them in its section already)
    connection = data.get(CONNECTION) or data
    values[CONNECTION] = {
        key: connection[key]
        for key in (CONF_VERIFY_SSL, CONF_CA_CERT, CONF_CLIENT_CERT, CONF_CLIENT_KEY)
        if key in connection
    }
    return values


class FirefoxSyncConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow of the Firefox Sync integration."""

    VERSION = 1

    def __init__(self) -> None:
        """Set up the flow."""
        self._discovery: dict[str, Any] = {}

    async def _async_check(self, data: Mapping[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
        """Check an entry's sources: its errors by field and the placeholders of their texts."""
        placeholders = {"error": "", "service": ""}
        try:
            ssl_context = await self.hass.async_add_executor_job(build_ssl_context, data)
        except TLSConfigError as err:
            placeholders["error"] = str(err)
            return {"base": "invalid_tls"}, placeholders
        session = async_get_clientsession(self.hass)
        if data.get(CONF_URL):
            try:
                await async_fetch_metrics(session, data[CONF_URL], data[CONF_METRICS_TOKEN], ssl_context)
            except MetricsAuthError:
                return {"base": "invalid_auth"}, placeholders
            except MetricsConnectionError as err:
                LOGGER.debug("Cannot fetch %s/metrics: %s", data[CONF_URL], err)
                placeholders["error"] = str(err)
                return {"base": "cannot_connect"}, placeholders
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unexpected error fetching %s/metrics", data[CONF_URL])
                return {"base": "unknown"}, placeholders
        for key in SERVICE_URLS:
            if not data.get(key):
                continue
            health = await async_fetch_health(session, SERVICES[key], data[key], ssl_context)
            if not health.up:
                placeholders["service"] = SERVICES[key].component
                placeholders["error"] = health.error or ""
                return {key: "service_down"}, placeholders
        return {}, placeholders

    def _entry_data(self, user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        """The entry's data from the form, and the errors of its fields."""
        errors: dict[str, str] = {}
        data: dict[str, Any] = {}
        if raw := (user_input.get(CONF_URL) or "").strip():
            if _has_credentials(raw):
                errors[CONF_URL] = "url_credentials"
            elif (url := _normalize_url(raw)) is None:
                errors[CONF_URL] = "invalid_url"
            else:
                data[CONF_URL] = url
            if not (token := (user_input.get(CONF_METRICS_TOKEN) or "").strip()):
                errors[CONF_METRICS_TOKEN] = "token_required"
            else:
                data[CONF_METRICS_TOKEN] = token
        for key in SERVICE_URLS:
            if raw := (user_input.get(key) or "").strip():
                if _has_credentials(raw):
                    errors[key] = "url_credentials"
                elif (url := _normalize_url(raw, websocket=key == CONF_PAIRING_URL)) is None:
                    errors[key] = "invalid_url"
                else:
                    data[key] = url
        if any(key in data for key in SERVICE_URLS) and CONF_SYNC_URL not in data and CONF_SYNC_URL not in errors:
            errors[CONF_SYNC_URL] = "sync_url_required"
        if not errors and CONF_URL not in data and CONF_SYNC_URL not in data:
            errors["base"] = "no_source"
        connection = user_input.get(CONNECTION) or {}
        data[CONF_VERIFY_SSL] = bool(connection.get(CONF_VERIFY_SSL, True))
        for key in (CONF_CA_CERT, CONF_CLIENT_CERT, CONF_CLIENT_KEY):
            if value := (connection.get(key) or "").strip():
                data[key] = value
        return data, errors

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The admin UI's metrics, the services' public URLs, or both, by hand."""
        errors: dict[str, str] = {}
        placeholders = {"error": "", "service": ""}
        if user_input is not None:
            data, errors = self._entry_data(user_input)
            if not errors:
                key = CONF_URL if CONF_URL in data else CONF_SYNC_URL
                self._async_abort_entries_match({key: data[key]})
                errors, placeholders = await self._async_check(data)
                if not errors:
                    return self.async_create_entry(title=URL(data[key]).host or data[key], data=data)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(_form_schema(), _suggested(user_input or {})),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        """The discovery of the Firefox Sync add-on, through the Supervisor."""
        config = discovery_info.config
        try:
            url = str(URL.build(scheme="http", host=config["host"], port=int(config["port"])))
            token = str(config["metrics_token"])
        except (KeyError, TypeError, ValueError):
            return self.async_abort(reason="invalid_discovery_info")
        # one entry per add-on: a new address or token (a reinstall) updates it
        await self.async_set_unique_id(discovery_info.slug)
        self._abort_if_unique_id_configured(updates={CONF_URL: url, CONF_METRICS_TOKEN: token})
        self._discovery = {
            CONF_URL: url,
            CONF_METRICS_TOKEN: token,
            CONF_ADDON_SLUG: discovery_info.slug,
            CONF_VERIFY_SSL: True,
            "name": discovery_info.name,
        }
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm the add-on's discovery."""
        errors: dict[str, str] = {}
        placeholders = {"addon": self._discovery["name"], "error": "", "service": ""}
        if user_input is not None:
            data = {key: value for key, value in self._discovery.items() if key != "name"}
            errors, found = await self._async_check(data)
            placeholders.update(found)
            if not errors:
                return self.async_create_entry(title=self._discovery["name"], data=data)
        return self.async_show_form(step_id="hassio_confirm", description_placeholders=placeholders, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The admin UI refused the token."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """A new metrics token."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        placeholders = {"url": entry.data.get(CONF_URL, ""), "error": "", "service": ""}
        if user_input is not None:
            data = {**entry.data, CONF_METRICS_TOKEN: user_input[CONF_METRICS_TOKEN].strip()}
            errors, found = await self._async_check(data)
            placeholders.update(found)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_METRICS_TOKEN: data[CONF_METRICS_TOKEN]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_METRICS_TOKEN): _SECRET}),
            description_placeholders=placeholders,
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """New sources or connection options for an entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        placeholders = {"error": "", "service": ""}
        if user_input is not None:
            data, errors = self._entry_data(user_input)
            if not errors:
                errors, placeholders = await self._async_check(data)
                if not errors:
                    if slug := entry.data.get(CONF_ADDON_SLUG):
                        data[CONF_ADDON_SLUG] = slug
                    return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(_form_schema(), _suggested(user_input or entry.data)),
            errors=errors,
            description_placeholders=placeholders,
        )
