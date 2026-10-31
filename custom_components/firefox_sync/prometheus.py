"""A small parser of the Prometheus text format (version 0.0.4).

The admin UI of firefox-sync serves its metrics at /metrics in this format
(admin/prometheus.go). Only what that page uses is needed: samples with optional
labels, # HELP and # TYPE comments, and the usual float values (NaN, +Inf, -Inf).
Timestamps after the value are accepted and ignored.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
import math
import re

_NAME = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")
_LABEL_NAME = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_ESCAPES = {"\\": "\\", '"': '"', "n": "\n"}


class PrometheusParseError(ValueError):
    """The text is not in the Prometheus text format."""


@dataclass(frozen=True)
class Sample:
    """One sample: a metric name, its labels and its value."""

    name: str
    labels: tuple[tuple[str, str], ...]
    value: float

    def label(self, name: str) -> str | None:
        """Return the value of a label, None when the sample has no such label."""
        for key, value in self.labels:
            if key == name:
                return value
        return None


@dataclass
class Metrics:
    """The samples of one scrape, by metric name, with their types."""

    samples: dict[str, list[Sample]] = field(default_factory=dict)
    types: dict[str, str] = field(default_factory=dict)

    def __contains__(self, name: object) -> bool:
        """Whether the scrape has at least one sample of this metric."""
        return name in self.samples

    def get(self, metric: str, /, **labels: str) -> float | None:
        """Return the value of the first sample of the metric with these labels, or None."""
        for sample in self.samples.get(metric, ()):
            if all(sample.label(key) == value for key, value in labels.items()):
                return sample.value
        return None

    def label_values(self, name: str, label: str) -> list[str]:
        """Return the values of one label over the samples of a metric, in order."""
        seen: list[str] = []
        for sample in self.samples.get(name, ()):
            value = sample.label(label)
            if value is not None and value not in seen:
                seen.append(value)
        return seen

    def __iter__(self) -> Iterator[Sample]:
        """Iterate over every sample."""
        for samples in self.samples.values():
            yield from samples


def _parse_value(text: str, line_no: int) -> float:
    lowered = text.lower()
    if lowered in ("+inf", "inf"):
        return math.inf
    if lowered == "-inf":
        return -math.inf
    if lowered == "nan":
        return math.nan
    try:
        return float(text)
    except ValueError as err:
        raise PrometheusParseError(f"line {line_no}: not a value: {text!r}") from err


def _parse_labels(text: str, pos: int, line_no: int) -> tuple[tuple[tuple[str, str], ...], int]:
    """Parse {a="b",...} from text[pos] == '{'; return the labels and the position after '}'."""
    labels: list[tuple[str, str]] = []
    pos += 1
    while True:
        while pos < len(text) and text[pos] in " \t":
            pos += 1
        if pos < len(text) and text[pos] == "}":
            return tuple(labels), pos + 1
        match = _LABEL_NAME.match(text, pos)
        if not match:
            raise PrometheusParseError(f"line {line_no}: bad label name")
        name = match.group(0)
        pos = match.end()
        if text[pos : pos + 2] != '="':
            raise PrometheusParseError(f'line {line_no}: expected =" after {name}')
        pos += 2
        value: list[str] = []
        while True:
            if pos >= len(text):
                raise PrometheusParseError(f"line {line_no}: unterminated label value")
            char = text[pos]
            if char == "\\":
                escaped = text[pos + 1 : pos + 2]
                value.append(_ESCAPES.get(escaped, "\\" + escaped))
                pos += 2
                continue
            if char == '"':
                pos += 1
                break
            value.append(char)
            pos += 1
        labels.append((name, "".join(value)))
        while pos < len(text) and text[pos] in " \t":
            pos += 1
        if pos < len(text) and text[pos] == ",":
            pos += 1
            continue
        if pos < len(text) and text[pos] == "}":
            return tuple(labels), pos + 1
        raise PrometheusParseError(f"line {line_no}: expected , or }} in the labels")


def parse(text: str) -> Metrics:
    """Parse a scrape in the Prometheus text format."""
    metrics = Metrics()
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            parts = line.split(None, 3)
            if len(parts) >= 4 and parts[1] == "TYPE":
                metrics.types[parts[2]] = parts[3].strip()
            continue
        match = _NAME.match(line)
        if not match:
            raise PrometheusParseError(f"line {line_no}: bad metric name")
        name = match.group(0)
        pos = match.end()
        labels: tuple[tuple[str, str], ...] = ()
        if pos < len(line) and line[pos] == "{":
            labels, pos = _parse_labels(line, pos, line_no)
        rest = line[pos:].split()
        if not rest or len(rest) > 2:
            raise PrometheusParseError(f"line {line_no}: expected a value after {name}")
        metrics.samples.setdefault(name, []).append(Sample(name, labels, _parse_value(rest[0], line_no)))
    return metrics
