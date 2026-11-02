"""What HACS, hassfest and a release need from the repository, checked without GitHub."""

from __future__ import annotations

import json
from pathlib import Path
import re
import tomllib

from PIL import Image

ROOT = Path(__file__).parent.parent
INTEGRATION = ROOT / "custom_components" / "firefox_sync"
MANIFEST = json.loads((INTEGRATION / "manifest.json").read_text())
EN = json.loads((INTEGRATION / "translations" / "en.json").read_text())
FLOW = (INTEGRATION / "config_flow.py").read_text()

# the keys of hacs.json that HACS's schema accepts (HACS_MANIFEST_JSON_SCHEMA, extra keys refused)
HACS_KEYS = {
    "name",
    "content_in_root",
    "country",
    "filename",
    "hacs",
    "hide_default_branch",
    "homeassistant",
    "persistent_directory",
    "render_readme",
    "zip_release",
}


def test_one_integration() -> None:
    """HACS installs the first folder under custom_components/: only ours there."""
    assert [p.name for p in (ROOT / "custom_components").iterdir() if p.is_dir()] == ["firefox_sync"]
    assert MANIFEST["domain"] == INTEGRATION.name


def test_hacs_json() -> None:
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert hacs["name"]
    assert set(hacs) <= HACS_KEYS
    # local brand images need 2026.3
    assert tuple(int(x) for x in hacs["homeassistant"].split(".")[:2]) >= (2026, 3)


def test_manifest() -> None:
    """The keys HACS and the Marketplace require, in hassfest's order (domain, name, the rest sorted)."""
    for key in ("domain", "name", "codeowners", "documentation", "issue_tracker", "version", "iot_class"):
        assert MANIFEST.get(key), key
    keys = list(MANIFEST)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])
    # X.Y.Z, or a pre-release X.Y.ZbN / X.Y.ZrcN (the release workflow marks those)
    assert re.fullmatch(r"\d+\.\d+\.\d+((a|b|rc)\d+)?", MANIFEST["version"])
    assert 1 <= len(MANIFEST["categories"]) <= 3


def test_versions_match() -> None:
    """The release tag is v<manifest version>; pyproject says the same."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == MANIFEST["version"]


def test_brand_images() -> None:
    """custom_components/<domain>/brand/ (Home Assistant 2026.3 and later, and HACS's brands check)."""
    brand = INTEGRATION / "brand"
    sizes = {p.name: Image.open(p).size for p in brand.glob("*.png")}
    assert sizes["icon.png"] == (256, 256)
    assert sizes.get("icon@2x.png", (512, 512)) == (512, 512)
    for name in ("logo.png", "dark_logo.png"):
        if name in sizes:
            assert 128 <= min(sizes[name]) <= 256, name
    for path in brand.glob("*.png"):
        image = Image.open(path)
        assert image.mode == "RGBA", path.name
        # trimmed: the subject reaches every edge, no transparent border
        assert image.getchannel("A").getbbox() == (0, 0, *image.size), path.name
        # anti-aliased edges, not a handful of flat colors
        assert len(image.getcolors(1 << 24)) > 16, path.name


def test_no_strings_json() -> None:
    """Custom integrations ship translations/en.json; strings.json is core's source file."""
    assert not (INTEGRATION / "strings.json").exists()


def test_flow_texts() -> None:
    """Every error and abort the flow can show has its English text."""
    errors = set(re.findall(r'errors\[[^\]]+\] = "(\w+)"', FLOW))
    errors |= set(re.findall(r'\{(?:"base"|key): "(\w+)"\}', FLOW))
    aborts = set(re.findall(r'reason="(\w+)"', FLOW))
    # raised by Home Assistant's helpers the flow calls
    aborts |= {"already_configured", "already_in_progress"}
    if "async_update_reload_and_abort" in FLOW and "_get_reauth_entry" in FLOW:
        aborts.add("reauth_successful")
    if "async_update_reload_and_abort" in FLOW and "_get_reconfigure_entry" in FLOW:
        aborts.add("reconfigure_successful")
    assert errors, "no errors found in the flow"
    assert errors <= set(EN["config"]["error"]), errors - set(EN["config"]["error"])
    assert aborts <= set(EN["config"]["abort"]), aborts - set(EN["config"]["abort"])
    for step in re.findall(r'step_id="(\w+)"', FLOW):
        assert step in EN["config"]["step"], step
