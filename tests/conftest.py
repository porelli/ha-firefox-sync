"""Fixtures of the Firefox Sync tests."""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.firefox_sync.const import CONF_METRICS_TOKEN, DOMAIN
from homeassistant.const import CONF_URL

FIXTURES = Path(__file__).parent / "fixtures"
URL = "http://ffs-admin.local:8090"
TOKEN = "s3cret-token"
SYNC = "https://sync.example.com"
AUTH = "https://api.example.com"
CONTENT = "https://accounts.example.com"
PROFILE = "https://profile.example.com"
PAIRING = "wss://pairing.example.com"


def fixture(name: str) -> str:
    """The text of a fixture file."""
    return (FIXTURES / name).read_text()


def mock_service(
    aioclient_mock: AiohttpClientMocker,
    base: str,
    *,
    lb_status: int = 200,
    heartbeat: dict | None = None,
    hb_status: int = 200,
    version: str | None = "1.2.3",
) -> None:
    """The Dockerflow endpoints of a service at base (an http(s) URL)."""
    aioclient_mock.get(f"{base}/__lbheartbeat__", status=lb_status, text="{}")
    aioclient_mock.get(f"{base}/__heartbeat__", status=hb_status, text=json.dumps(heartbeat or {}))
    if version is None:
        aioclient_mock.get(f"{base}/__version__", status=404)
    else:
        aioclient_mock.get(
            f"{base}/__version__",
            text=json.dumps({"source": "https://github.com/x", "version": version, "commit": "abc", "build": ""}),
        )


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ in every test."""
    return


@pytest.fixture
def metrics_text() -> str:
    """A scrape of the admin UI without the self-hosted accounts."""
    return fixture("metrics.txt")


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    """A config entry of an admin UI, added by hand."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="ffs-admin.local",
        data={CONF_URL: URL, CONF_METRICS_TOKEN: TOKEN},
    )


@pytest.fixture(scope="session")
def tls_material() -> dict[str, str]:
    """A CA, and a client certificate it signed with its key, as PEM."""
    now = datetime.datetime.now(datetime.UTC)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ffs test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "home-assistant")]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(ca_key, hashes.SHA256())
    )
    return {
        "ca_pem": ca.public_bytes(serialization.Encoding.PEM).decode(),
        "cert_pem": cert.public_bytes(serialization.Encoding.PEM).decode(),
        "key_pem": key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode(),
    }


@pytest.fixture
def tls_files(tls_material: dict[str, str], tmp_path: Path) -> dict[str, str]:
    """The TLS material, with the CA also in a file."""
    ca_path = tmp_path / "ca.pem"
    ca_path.write_text(tls_material["ca_pem"])
    return {**tls_material, "ca_path": str(ca_path)}
