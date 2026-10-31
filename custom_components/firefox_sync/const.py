"""Constants of the Firefox Sync integration."""

from datetime import timedelta
import logging

DOMAIN = "firefox_sync"
LOGGER = logging.getLogger(__package__)

# the admin UI's metrics: its URL (CONF_URL, "url") and ADMIN_METRICS_TOKEN
CONF_METRICS_TOKEN = "metrics_token"
# the public URLs of the services, without the admin UI (api.SERVICES)
CONF_SYNC_URL = "sync_url"
CONF_AUTH_URL = "auth_url"
CONF_CONTENT_URL = "content_url"
CONF_PROFILE_URL = "profile_url"
CONF_PAIRING_URL = "pairing_url"
SERVICE_URLS = (CONF_SYNC_URL, CONF_AUTH_URL, CONF_CONTENT_URL, CONF_PROFILE_URL, CONF_PAIRING_URL)
# the connection: TLS verification, a custom CA, a client certificate and its key (PEM text
# or a path each)
CONF_VERIFY_SSL = "verify_ssl"
CONF_CA_CERT = "ca_cert"
CONF_CLIENT_CERT = "client_cert"
CONF_CLIENT_KEY = "client_key"
TLS_OPTIONS = (CONF_VERIFY_SSL, CONF_CA_CERT, CONF_CLIENT_CERT, CONF_CLIENT_KEY)
# the slug of the add-on that sent the Supervisor's discovery (hassio entries only)
CONF_ADDON_SLUG = "addon_slug"

# the admin UI computes most of its metrics every minute (health, sizes) or hour
# (certificates, public checks): no point in polling faster
SCAN_INTERVAL = timedelta(seconds=60)
REQUEST_TIMEOUT = 15
