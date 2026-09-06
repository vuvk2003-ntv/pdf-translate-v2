"""Deterministic guards for technical data that translation must not change."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class InvariantDifference:
    category: str
    missing: tuple[str, ...]
    added: tuple[str, ...]


class TechnicalInvariantError(ValueError):
    """Raised when translated text changes deterministic technical content."""

    def __init__(self, differences: list[InvariantDifference]) -> None:
        self.differences = tuple(differences)
        details = "; ".join(
            f"{item.category}: missing={list(item.missing)!r}, added={list(item.added)!r}"
            for item in differences
        )
        super().__init__(f"technical invariants changed ({details})")


URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
WINDOWS_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z]:\\|\\\\)[^\s<>\"|?*]+"
)
POSIX_PATH_PATTERN = re.compile(r"(?<![\w:])(?:\.{0,2}/|/)[\w.-]+(?:/[\w.-]+)+")
FILE_NAME_PATTERN = re.compile(
    r"(?<![\w.-])[\w.-]+\.(?:pdf|jsonl|csv|txt|xml|yaml|yml|ini|cfg|log|py|"
    r"ps1|sh|exe|dll|bin|hex|gxw|gx3|prx)(?![\w.-])",
    re.IGNORECASE,
)
IPV4_PATTERN = re.compile(
    r"(?<![\d.])(?:25[0-5]|2[0-4]\d|1?\d?\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?![\d.])"
)
NETWORK_ENDPOINT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:TCP|UDP)/\d{1,5}(?![A-Za-z0-9])"
)
TECHNICAL_IDENTIFIER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"0x[0-9A-Fa-f]+|"
    r"[A-Z]{1,3}\[\d+\]|"
    r"(?:ZR|D|M|X|Y|R|P|Z|B|W|L|F|V)\d+|"
    r"(?:AL|ERR|ER|E)[.-]\d+|"
    r"(?:CN|COM)\d+|"
    r"(?=[A-Z0-9][A-Z0-9._/-]{3,})(?=[A-Z0-9._/-]*[A-Z])"
    r"(?=[A-Z0-9._/-]*\d)[A-Z0-9]+(?:[._/-][A-Z0-9]+)*"
    r")(?![A-Za-z0-9_])"
)
STANDARD_ID_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:ISO|IEC|EN|KS|DIN|JIS)\s+[A-Z]?\s*\d+"
    r"(?:-\d+)*(?::\d{4})?(?![A-Za-z0-9])"
)
VERSION_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?:Rev\.[A-Za-z0-9]+|v\d+(?:\.\d+){1,3})"
    r"(?![A-Za-z0-9])"
)
DOCUMENT_ID_PATTERN = re.compile(
    r"\b(?:SPEC|DWG|Document)\s+No\.\s*[A-Za-z0-9._/-]+|"
    r"\b(?:P/N|S/N|ECN|ECR|LOT)\s*:?\s*[A-Za-z0-9._/-]+"
)
NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
    r"(?![A-Za-z0-9_]|\.\d)"
)
RANGE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.])[-+]?(?:\d+(?:\.\d+)?)\s*(?:±|–|-)\s*"
    r"[-+]?(?:\d+(?:\.\d+)?)(?![A-Za-z0-9_.])"
)
UNIT_SOURCE = (
    r"VAC|VDC|mA|MPa|kPa|Pa|N·m|N\.m|mm/s|m/s|rpm|kHz|Hz|ms|"
    r"µs|μs|°C|mm|cm|kW|W|sec|%"
)
UNIT_PATTERN = re.compile(rf"(?<![A-Za-z0-9])(?:{UNIT_SOURCE})(?![A-Za-z0-9])")
ENGINEERING_QUANTITY_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9_.])(?P<value>[-+]?(?:\d+(?:\.\d+)?))\s*"
    rf"(?P<unit>{UNIT_SOURCE})(?![A-Za-z0-9])"
)
SINGLE_UNIT_QUANTITY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.])[-+]?(?:\d+(?:\.\d+)?)\s*(?:V|A|N|m)"
    r"(?![A-Za-z0-9])"
)
POLARITY_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:ON|OFF)(?![A-Za-z0-9])")
EXECUTABLE_CODE_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:DFROM_M|DTO_M|MOV|BMOV|SET|RST|CASE|IF|THEN|"
    r"TON|MOVJ|MOVL|NWAIT)(?![A-Za-z0-9_])"
)
PLACEHOLDER_PATTERN = re.compile(
    r"\{\{[^{}\r\n]+\}\}|\{[A-Za-z_][\w.-]*\}|"
    r"\$\{[A-Za-z_][\w.-]*\}|\$[A-Za-z_][\w.-]*|%(?:\d+|[sdif])"
)
TAG_PATTERN = re.compile(
    r"<(/?)([A-Za-z][A-Za-z0-9:_-]*)(?:\s[^<>]*?)?(/?)>"
)


def _regex_tokens(pattern: re.Pattern[str], text: str) -> list[str]:
    return [match.group(0) for match in pattern.finditer(text)]


def _url_tokens(text: str) -> list[str]:
    return [token.rstrip(".,;:!?)") for token in _regex_tokens(URL_PATTERN, text)]


def _tag_tokens(text: str) -> list[str]:
    return [match.group(0) for match in TAG_PATTERN.finditer(text)]


def _normalise_quantity_units(text: str) -> str:
    """Make harmless value/unit spacing and equivalent micro signs comparable."""
    def replace(match: re.Match[str]) -> str:
        unit = match.group("unit").replace("μ", "µ").replace("N.m", "N·m")
        return f'{match.group("value")} {unit}'

    return ENGINEERING_QUANTITY_PATTERN.sub(replace, text)


def _number_tokens(text: str) -> list[str]:
    return _regex_tokens(NUMBER_PATTERN, _normalise_quantity_units(text))


def _unit_tokens(text: str) -> list[str]:
    return _regex_tokens(UNIT_PATTERN, _normalise_quantity_units(text))


def _quantity_tokens(text: str) -> list[str]:
    normalised = _normalise_quantity_units(text)
    return [
        f'{match.group("value")} {match.group("unit")}'
        for match in ENGINEERING_QUANTITY_PATTERN.finditer(normalised)
    ]


def _validate_tag_nesting(text: str) -> None:
    stack: list[str] = []
    for match in TAG_PATTERN.finditer(text):
        closing, name, self_closing = match.groups()
        name = name.lower()
        if self_closing:
            continue
        if closing:
            if not stack or stack[-1] != name:
                raise ValueError("structural tags are malformed or cross-nested")
            stack.pop()
        else:
            stack.append(name)
    if stack:
        raise ValueError("structural tags are not closed")


def _expanded(counter: Counter[str]) -> tuple[str, ...]:
    return tuple(
        token
        for token in sorted(counter)
        for _ in range(counter[token])
    )


TOKEN_CHECKS: tuple[tuple[str, Callable[[str], list[str]]], ...] = (
    ("placeholders", lambda text: _regex_tokens(PLACEHOLDER_PATTERN, text)),
    ("structural tags", _tag_tokens),
    ("URLs", _url_tokens),
    ("Windows paths", lambda text: _regex_tokens(WINDOWS_PATH_PATTERN, text)),
    ("POSIX paths", lambda text: _regex_tokens(POSIX_PATH_PATTERN, text)),
    ("file names", lambda text: _regex_tokens(FILE_NAME_PATTERN, text)),
    ("IPv4 addresses", lambda text: _regex_tokens(IPV4_PATTERN, text)),
    ("network endpoints", lambda text: _regex_tokens(NETWORK_ENDPOINT_PATTERN, text)),
    (
        "technical identifiers",
        lambda text: _regex_tokens(TECHNICAL_IDENTIFIER_PATTERN, text),
    ),
    ("standard IDs", lambda text: _regex_tokens(STANDARD_ID_PATTERN, text)),
    ("versions", lambda text: _regex_tokens(VERSION_PATTERN, text)),
    ("document IDs", lambda text: _regex_tokens(DOCUMENT_ID_PATTERN, text)),
    ("numbers", _number_tokens),
    ("engineering ranges", lambda text: _regex_tokens(RANGE_PATTERN, text)),
    ("engineering units", _unit_tokens),
    ("engineering quantities", _quantity_tokens),
    (
        "single-unit quantities",
        lambda text: _regex_tokens(SINGLE_UNIT_QUANTITY_PATTERN, text),
    ),
    ("ON/OFF polarity", lambda text: _regex_tokens(POLARITY_PATTERN, text)),
    (
        "executable code tokens",
        lambda text: _regex_tokens(EXECUTABLE_CODE_TOKEN_PATTERN, text),
    ),
)


def validate_technical_invariants(source: str, translated: str) -> None:
    """Reject changed, removed, duplicated, or invented deterministic tokens."""
    differences: list[InvariantDifference] = []
    for category, extractor in TOKEN_CHECKS:
        source_tokens = Counter(extractor(source))
        translated_tokens = Counter(extractor(translated))
        if source_tokens == translated_tokens:
            continue
        differences.append(
            InvariantDifference(
                category,
                _expanded(source_tokens - translated_tokens),
                _expanded(translated_tokens - source_tokens),
            )
        )

    for label, text in (("source", source), ("translation", translated)):
        try:
            _validate_tag_nesting(text)
        except ValueError as error:
            differences.append(
                InvariantDifference(f"{label} tag structure", (str(error),), ())
            )

    if differences:
        raise TechnicalInvariantError(differences)
