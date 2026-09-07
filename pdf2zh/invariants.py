"""Deterministic guards for technical data that translation must not change."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

# Bump when classifier/validation semantics change; old cache entries stay on disk.
TRANSLATION_RULES_VERSION = "code4life-translation-v2"


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
    r"(?:OK|NG)#\d+|PCW|"
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
CODE_OPERAND = r"(?:0x[0-9A-Fa-f]+|[A-Z]{1,3}\[\d+\]|[A-Z]{1,3}\d+|[-+]?\d+(?:\.\d+)?)"
CODE_STATEMENT_PATTERN = re.compile(
    rf"(?<!\w)(?:(?:DFROM_M|DTO_M|MOV|BMOV|SET|RST|TON|MOVJ|MOVL)"
    rf"(?:[ \t]+{CODE_OPERAND})+(?!\w)|NWAIT(?!\w))"
)


def is_executable_code_line(text: str) -> bool:
    """Recognize supported code syntax, not an opcode-shaped English prefix."""
    text = re.sub(r"^\s*\d+\s*[:)]\s*", "", text).strip()
    if CODE_STATEMENT_PATTERN.fullmatch(text):
        return True
    conditional = re.fullmatch(r"IF\s+[A-Za-z_][\w.]*\s+THEN\s+(.+)", text)
    return bool(conditional and CODE_STATEMENT_PATTERN.fullmatch(conditional[1]))


def _executable_tokens(text: str) -> list[str]:
    tokens = []
    for line in text.splitlines():
        if is_executable_code_line(line):
            tokens.extend(_regex_tokens(EXECUTABLE_CODE_TOKEN_PATTERN, line))
        else:
            for match in CODE_STATEMENT_PATTERN.finditer(line):
                tokens.extend(_regex_tokens(EXECUTABLE_CODE_TOKEN_PATTERN, match[0]))
    return tokens
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
        _executable_tokens,
    ),
)

_NAMED_OBJECT_PATTERN = re.compile(
    r"\b(?:(?:axis|trục)\s+([XYZABC]|\d+)|([XYZABC])\s*(?:axis|축|轴|軸)|"
    r"(?:parameter|tham số|参数|參數|파라미터)\s+(\d+))\b", re.IGNORECASE
)
_ASSOCIATION_VALUE = r"[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?!\w|\.\d)"
_FORWARD_VALUE = re.compile(
    rf"\s*(?:(?:=|:)|(?:to|at|is|thành|bằng|là)|(?:값을|값은|값|을|를|은|는))?"
    rf"\s*({_ASSOCIATION_VALUE})", re.IGNORECASE
)
_REVERSE_VALUE = re.compile(
    rf"(?<![\w.])({_ASSOCIATION_VALUE})\s+(?:cho|vào|to|into)\s*$", re.IGNORECASE
)


def _object_values(text: str) -> dict[str, Counter[str]]:
    """Explicit adjacent assignments only; not a general vendor-language parser."""
    text = _normalise_quantity_units(TAG_PATTERN.sub("", text))
    objects = [(m.start(), m.end(), m[0]) for m in TECHNICAL_IDENTIFIER_PATTERN.finditer(text)]
    for match in _NAMED_OBJECT_PATTERN.finditer(text):
        axis = match[1] or match[2]
        name = f"axis:{axis.upper()}" if axis else f"parameter:{match[3]}"
        objects.append((match.start(), match.end(), name))
    values: dict[str, Counter[str]] = {}
    for start, end, name in objects:
        forward = _FORWARD_VALUE.match(text, end)
        reverse = _REVERSE_VALUE.search(text[max(0, start - 50):start])
        value = forward or reverse
        if value:
            values.setdefault(name, Counter())[value[1]] += 1
    return values


def _association_differences(source: str, translated: str) -> list[InvariantDifference]:
    source_values, target_values = _object_values(source), _object_values(translated)
    return [
        InvariantDifference(f"value association {key}", _expanded(source_values[key]), _expanded(target_values[key]))
        for key in sorted(source_values.keys() & target_values.keys())
        if source_values[key] != target_values[key]
    ]


_SEMANTIC_CUES = (
    ("prohibition", r"\b(?:do not|must not|shall not|never|is not permitted|is not allowed)\b|하지\s*마|금지|禁止|不得|严禁|嚴禁|请勿|請勿|切勿",
     r"\b(?:không|cấm|đừng)\b"),
    ("mandatory", r"\b(?:must|shall)\b(?!\s+not\b)|반드시|하여야\s*한다|해야\s*한다|必须|必須",
     r"\b(?:phải|bắt buộc|nhất thiết)\b"),
    ("recommended", r"\b(?:should|recommended)\b|권장|建议|建議",
     r"\b(?:nên|khuyến nghị|khuyến cáo)\b"),
    ("permission", r"\b(?:is permitted|is allowed)\b",
     r"\b(?:được phép|cho phép|có thể)\b"),
)
_COMPILED_SEMANTIC_CUES = tuple(
    (name, re.compile(src, re.IGNORECASE), re.compile(dst, re.IGNORECASE))
    for name, src, dst in _SEMANTIC_CUES
)
_ORDER_PATTERN = re.compile(r"\b(before|after|trước khi|sau khi|trước|sau)\b", re.IGNORECASE)


def _anchored_order(text: str) -> tuple[str, str] | None:
    """Compare before/after only when both actions have one distinct stable anchor."""
    text = TAG_PATTERN.sub("", text)
    matches = list(_ORDER_PATTERN.finditer(text))
    if len(matches) != 1:
        return None
    relation = matches[0]
    if text[:relation.start()].strip():
        left, right = text[:relation.start()], text[relation.end():]
    else:
        pieces = text[relation.end():].split(",", 1)
        if len(pieces) != 2:
            return None
        right, left = pieces
    def anchors(value: str) -> set[str]:
        return set(_regex_tokens(TECHNICAL_IDENTIFIER_PATTERN, value) + _regex_tokens(POLARITY_PATTERN, value))
    first, second = anchors(left), anchors(right)
    if len(first) != 1 or len(second) != 1 or first == second:
        return None
    pair = (next(iter(first)), next(iter(second)))
    return pair if relation[0].casefold().startswith(("before", "trước")) else pair[::-1]


def validate_lightweight_semantics(source: str, translated: str, target_language: str | None) -> None:
    """Flag supported cue loss/direction changes without a second model pass.

    Only Vietnamese target cues and explicitly anchored order are checked.
    Passing this guard does not prove semantic equivalence or negation scope.
    """
    if target_language != "vi":
        return
    source, translated = TAG_PATTERN.sub("", source), TAG_PATTERN.sub("", translated)
    differences = [
        InvariantDifference(f"semantic {name}", (name,), ())
        for name, source_pattern, target_pattern in _COMPILED_SEMANTIC_CUES
        if source_pattern.search(source) and not target_pattern.search(translated)
    ]
    source_order, target_order = _anchored_order(source), _anchored_order(translated)
    if source_order and target_order and source_order != target_order:
        differences.append(InvariantDifference("semantic order", source_order, target_order))
    if differences:
        raise TechnicalInvariantError(differences)


def validate_technical_invariants(source: str, translated: str) -> None:
    """Reject changed, removed, duplicated, or invented deterministic tokens."""
    differences: list[InvariantDifference] = _association_differences(source, translated)
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
