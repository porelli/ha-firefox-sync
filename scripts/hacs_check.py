"""The HACS action's checks that need no GitHub repository, run on this checkout's committed files.

    python3 scripts/hacs_check.py <a clone of github.com/hacs/integration> [repository root]

Uses HACS's own schemas (custom_components/hacs/utils/validate.py of the clone: hacs.json,
the integration manifest, its categories) and does what its validators do with the tree
(hacsjson, integration_manifest, integration_categories, brands, information), on the files
`git ls-files` lists, the ones a push would publish. The others need the published repository
and are listed as such: description, topics, issues, archived, a release, the license as GitHub
detects it. Run it with an environment that has voluptuous, awesomeversion and homeassistant
(the dev venv). The exit status is the number of failed checks.
"""

from __future__ import annotations

import collections
import importlib
import json
from pathlib import Path
import subprocess
import sys
import types

HACS = Path(sys.argv[1]).resolve() / "custom_components" / "hacs"
ROOT = Path(sys.argv[2] if len(sys.argv) > 2 else ".").resolve()
REMOTE_ONLY = {
    "description": "the repository has a description",
    "topics": "the repository has topics",
    "issues": "issues are turned on",
    "archived": "the repository is not archived",
    "releases": "at least one release (the catalog review)",
    "license": "GitHub identifies the license (SPDX id, OSI approved)",
}


def load_hacs_validate():
    """HACS's validate module without HACS's package __init__ (it needs GitHub's client)."""
    for name, path in (("custom_components", HACS.parent), ("custom_components.hacs", HACS)):
        module = types.ModuleType(name)
        module.__path__ = [str(path)]
        sys.modules[name] = module
    # const.py imports one constant of aiogithubapi
    for name in ("aiogithubapi", "aiogithubapi.common", "aiogithubapi.common.const"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["aiogithubapi.common.const"].ACCEPT_HEADERS = collections.defaultdict(str)
    return importlib.import_module("custom_components.hacs.utils.validate")


def main() -> int:
    validate = load_hacs_validate()
    tree = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.split()
    failures = 0

    def result(ok: bool | None, check: str, detail: str = "") -> None:
        nonlocal failures
        failures += ok is False
        label = {True: "PASS", False: "FAIL", None: "WARN"}[ok]
        print(f"{label}: {check}{f': {detail}' if detail else ''}")

    roots = sorted({p.split("/")[1] for p in tree if p.startswith("custom_components/") and p.count("/") >= 2})
    result(len(roots) == 1, "structure (one integration under custom_components/)", ", ".join(roots))
    integration = f"custom_components/{roots[0]}"

    if "hacs.json" in tree:
        raw = json.loads((ROOT / "hacs.json").read_text())
        try:
            validate.HACS_MANIFEST_JSON_SCHEMA(raw)
            result(True, "hacsjson", json.dumps(raw))
        except validate.vol.Invalid as err:
            result(False, "hacsjson", str(err))
    else:
        result(False, "hacsjson", "no hacs.json")

    manifest_path = f"{integration}/manifest.json"
    if manifest_path in tree:
        manifest = json.loads((ROOT / manifest_path).read_text())
        try:
            validate.INTEGRATION_MANIFEST_JSON_SCHEMA(manifest)
            result(True, "integration_manifest", f"version {manifest['version']}")
        except validate.vol.Invalid as err:
            result(False, "integration_manifest", str(err))
        if "categories" not in manifest:
            result(None, "integration_categories", "no categories (a warning)")
        else:
            try:
                validate.INTEGRATION_MANIFEST_CATEGORIES_SCHEMA(manifest)
                result(True, "integration_categories", ", ".join(manifest["categories"]))
            except validate.vol.Invalid as err:
                result(False, "integration_categories", str(err))
    else:
        result(False, "integration_manifest", f"no {manifest_path}")

    icon = f"{integration}/brand/icon.png"
    result(
        icon in tree, "brands", icon if icon in tree else f"no {icon} (the fallback, the brands repository, is remote)"
    )

    readme = [p for p in tree if "/" not in p and p.lower() in ("readme", "readme.md", "info", "info.md")]
    result(bool(readme), "information", ", ".join(readme))

    licenses = [p for p in tree if "/" not in p and p.upper().startswith(("LICENSE", "LICENCE", "COPYING"))]
    result(bool(licenses), "a license file (GitHub's detection is remote)", ", ".join(licenses))

    for check, what in REMOTE_ONLY.items():
        print(f"NEEDS GITHUB: {check}: {what}")
    print(f"== {failures} failed")
    return failures


if __name__ == "__main__":
    sys.exit(main())
