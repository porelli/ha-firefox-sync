"""Tests of the Prometheus text parser."""

from __future__ import annotations

import math

import pytest

from custom_components.firefox_sync.prometheus import PrometheusParseError, parse

from .conftest import fixture


def test_parse_admin_ui_scrape() -> None:
    """The metrics of admin/prometheus.go."""
    metrics = parse(fixture("metrics.txt"))
    assert metrics.get("ffs_sync_accounts") == 3
    assert metrics.get("ffs_sync_max_users") == 5
    assert metrics.get("ffs_up", component="Storage database") == 0
    assert metrics.get("ffs_up", component="Sync server") == 1
    assert metrics.label_values("ffs_up", "component") == [
        "Sync server",
        "Tokenserver database",
        "Storage database",
    ]
    assert metrics.get("ffs_database_bytes", database="syncstorage") == 10485760
    assert metrics.get("ffs_sync_error_ratio") == pytest.approx(0.025)
    assert metrics.get("ffs_statsd_ms_total", name="syncstorage_storage_get_collections") == 1500
    assert metrics.get("ffs_sync_errors_total", service="storage", status="500") == 4
    assert metrics.types["ffs_notify_failures_total"] == "counter"
    assert "ffs_accounts" not in metrics
    assert metrics.get("ffs_accounts") is None
    assert metrics.get("ffs_up", component="nope") is None


def test_label_escapes_and_spacing() -> None:
    """Escaped quotes, backslashes and new lines in label values; spaces around labels."""
    metrics = parse('m{a="x\\"y",b="c\\\\d", c="line\\nbreak" } 1\nplain 2 1700000000000\nempty{} 3\n')
    sample = next(iter(metrics.samples["m"]))
    assert sample.label("a") == 'x"y'
    assert sample.label("b") == "c\\d"
    assert sample.label("c") == "line\nbreak"
    assert sample.label("missing") is None
    # a timestamp after the value is ignored
    assert metrics.get("plain") == 2
    assert metrics.get("empty") == 3


def test_special_values() -> None:
    """NaN and the infinities."""
    metrics = parse("a NaN\nb +Inf\nc -Inf\nd 1e3\n")
    assert math.isnan(metrics.get("a"))
    assert metrics.get("b") == math.inf
    assert metrics.get("c") == -math.inf
    assert metrics.get("d") == 1000


def test_comments_and_blank_lines() -> None:
    """Comments that are not HELP or TYPE, and blank lines, are skipped."""
    metrics = parse("# just a comment\n\n# TYPE x counter\nx 1\n")
    assert metrics.types == {"x": "counter"}
    assert [s.name for s in metrics] == ["x"]


@pytest.mark.parametrize(
    "text",
    [
        "1bad 1",
        "name",
        "name not-a-number",
        'name{a="unterminated} 1',
        "name{a=1} 1",
        'name{a="b" c="d"} 1',
        "name 1 2 3",
        '{a="b"} 1',
    ],
)
def test_invalid(text: str) -> None:
    """What is not the text format is refused."""
    with pytest.raises(PrometheusParseError):
        parse(text)


def test_parse_real_scrape() -> None:
    """A scrape of the Firefox Sync add-on's admin UI (tests/run-firefox-sync.sh in the VM)."""
    metrics = parse(fixture("metrics_addon.txt"))
    assert metrics.get("ffs_sync_accounts") == 1
    assert metrics.get("ffs_sync_full") == 1
    assert metrics.get("ffs_backup_last_ok", scope="sync") == 1
    assert "Tokenserver database" in metrics.label_values("ffs_up", "component")
    assert metrics.label_values("ffs_database_bytes", "database") == ["Admin UI", "Sync storage", "Tokenserver"]
    assert any(s.name == "ffs_statsd_total" for s in metrics)
