"""Deterministic guards for technical data that translation must not change."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

# Bump when classifier/validation semantics change; old cache entries stay on disk.
TRANSLATION_RULES_VERSION = "code4life-translation-v5"

# Reviewed, exact source aliases only. Adding or changing an entry also requires
# a translation-rules revision so an older cache cannot bypass the new invariant.
VERIFIED_PROPER_NAMES = ("Inovance Technology",)
_STYLE_TAG_PATTERN = re.compile(r"</?s[123]>", re.IGNORECASE)
_ASCII_NAME_CHARACTER = r"A-Za-z0-9_"


@dataclass(frozen=True)
class ProperNameSpan:
    start: int
    end: int
    name: str


@dataclass(frozen=True)
class ProtectedLiteralSpan:
    """A source slice that must cross the translation provider unchanged."""

    start: int
    end: int
    literal: str
    kind: str


class VerifiedProperNameError(ValueError):
    """Raised when a reviewed proper-name span is lost, changed, or invented."""


def find_verified_proper_names(
    text: str,
    names: Iterable[str] = VERIFIED_PROPER_NAMES,
) -> tuple[ProperNameSpan, ...]:
    """Return leftmost-longest, non-overlapping, exact reviewed name spans."""
    candidates: list[ProperNameSpan] = []
    reviewed = tuple(dict.fromkeys(name for name in names if name))
    for name in reviewed:
        pattern = re.compile(
            rf"(?<![{_ASCII_NAME_CHARACTER}]){re.escape(name)}"
            rf"(?![{_ASCII_NAME_CHARACTER}])"
        )
        candidates.extend(
            ProperNameSpan(match.start(), match.end(), name)
            for match in pattern.finditer(text)
        )
    candidates.sort(key=lambda span: (span.start, -(span.end - span.start), span.name))
    selected: list[ProperNameSpan] = []
    cursor = -1
    for candidate in candidates:
        if candidate.start < cursor:
            continue
        selected.append(candidate)
        cursor = candidate.end
    return tuple(selected)


def protected_verified_proper_names(
    text: str,
    terminology: Mapping[str, str] | None = None,
) -> tuple[ProperNameSpan, ...]:
    """Return reviewed spans whose exact alias has no document override."""
    overrides = terminology or {}
    return tuple(
        span
        for span in find_verified_proper_names(text)
        if span.name not in overrides
    )


def is_standalone_verified_proper_name(
    text: str,
    terminology: Mapping[str, str] | None = None,
) -> bool:
    """Whether visible content is only reviewed names and harmless punctuation."""
    spans = protected_verified_proper_names(text, terminology)
    if not spans:
        return False
    remainder: list[str] = []
    cursor = 0
    for span in spans:
        remainder.append(text[cursor : span.start])
        cursor = span.end
    remainder.append(text[cursor:])
    visible_remainder = _STYLE_TAG_PATTERN.sub("", "".join(remainder))
    return all(
        character.isspace() or unicodedata.category(character).startswith("P")
        for character in visible_remainder
    )


def validate_verified_proper_names(
    source: str,
    target: str,
    terminology: Mapping[str, str] | None = None,
) -> None:
    """Require exact occurrence counts unless document terminology overrides an alias."""
    overrides = terminology or {}
    active_names = tuple(name for name in VERIFIED_PROPER_NAMES if name not in overrides)
    source_counts = Counter(
        span.name for span in find_verified_proper_names(source, active_names)
    )
    target_counts = Counter(
        span.name for span in find_verified_proper_names(target, active_names)
    )
    for name in active_names:
        if source_counts[name] == target_counts[name]:
            continue
        raise VerifiedProperNameError(
            f"verified proper-name span {name!r} occurrence count changed "
            f"from {source_counts[name]} to {target_counts[name]}"
        )


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
_WINDOWS_PATH_PREFIX = r"(?:[A-Za-z]:\\|\\\\)"
_TECHNICAL_FILE_EXTENSION = (
    r"pdf|jsonl|csv|txt|xml|yaml|yml|ini|cfg|log|py|ps1|sh|exe|dll|bin|"
    r"hex|gxw|gx3|prx"
)
WINDOWS_PATH_PATTERN = re.compile(
    rf"(?<![A-Za-z0-9])(?:"
    # A bounded path/template with a known file extension may contain Unicode,
    # spaces and typographic apostrophes.  This covers filename templates such
    # as C:\\J1C\\ 오늘날짜-시’분’초’.txt without freezing following prose.
    rf"{_WINDOWS_PATH_PREFIX}[^<>:\"/|?*;\r\n]{{1,240}}?\."
    rf"(?:{_TECHNICAL_FILE_EXTENSION})(?=$|[\s,;:!?\)\]\}}\"'”’])|"
    # Keep the compact extensionless/path-token behavior used by earlier rules.
    rf"{_WINDOWS_PATH_PREFIX}[^\s<>\"|?*]+"
    rf")",
    re.IGNORECASE,
)
POSIX_PATH_PATTERN = re.compile(r"(?<![\w:])(?:\.{0,2}/|/)[\w.-]+(?:/[\w.-]+)+")
FILE_NAME_PATTERN = re.compile(
    rf"(?<![\w.-])[\w.-]+\.(?:{_TECHNICAL_FILE_EXTENSION})(?![\w.-])",
    re.IGNORECASE,
)
DOCUMENT_LABEL_PATTERN = re.compile(r"(?<!\w)Page\s+No\.?(?!\w)")


def find_windows_path_literals(text: str) -> tuple[ProtectedLiteralSpan, ...]:
    """Return complete Windows path/template literals in source order."""
    return tuple(
        ProtectedLiteralSpan(match.start(), match.end(), match.group(0), "Windows path")
        for match in WINDOWS_PATH_PATTERN.finditer(text)
    )


def find_document_label_literals(text: str) -> tuple[ProtectedLiteralSpan, ...]:
    """Return fixed document-control labels embedded in a larger text run."""
    return tuple(
        ProtectedLiteralSpan(
            match.start(), match.end(), match.group(0), "document-control label"
        )
        for match in DOCUMENT_LABEL_PATTERN.finditer(text)
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
TECHNICAL_SYNTAX_PATTERN = re.compile(r"/\*|\*/|//|[;$*\[\]{}]")
INLINE_CODE_PATTERN = re.compile(r"`([^`\r\n]+)`")


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


def _technical_syntax_tokens(text: str) -> list[str]:
    """Protect syntax only inside explicit code spans or recognized code lines."""
    tokens: list[str] = []
    tokens.extend(
        token
        for match in INLINE_CODE_PATTERN.finditer(text)
        for token in _regex_tokens(TECHNICAL_SYNTAX_PATTERN, match.group(1))
    )
    for line in text.splitlines():
        if is_executable_code_line(line):
            tokens.extend(_regex_tokens(TECHNICAL_SYNTAX_PATTERN, line))
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


def _windows_path_tokens(text: str) -> list[str]:
    return [
        token.rstrip(".,!?)”’")
        for token in _regex_tokens(WINDOWS_PATH_PATTERN, text)
    ]


def _file_name_tokens(text: str) -> list[str]:
    # A terminal full stop belongs to prose, not a Windows filename.  Work on a
    # comparison-only copy so provider masking and the source bytes stay exact.
    comparison = re.sub(r"(?<=[A-Za-z0-9])([.!?])(?=\s|$)", "", text)
    return _regex_tokens(FILE_NAME_PATTERN, comparison)


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
    ("Windows paths", _windows_path_tokens),
    ("POSIX paths", lambda text: _regex_tokens(POSIX_PATH_PATTERN, text)),
    ("file names", _file_name_tokens),
    ("document-control labels", lambda text: _regex_tokens(DOCUMENT_LABEL_PATTERN, text)),
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
    ("technical syntax", _technical_syntax_tokens),
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
_HANGUL_PATTERN = re.compile(r"[\uac00-\ud7a3]")
_KOREAN_AMBIGUOUS_TERM_PATTERN = re.compile(r"검수|시운전|대응|공용화")
_ASCII_WORD_PATTERN = re.compile(r"(?<!\w)[A-Za-z][A-Za-z0-9]*(?:[ /&.-]+[A-Za-z0-9]+)*(?!\w)")
_KOREAN_NEED_PATTERN = re.compile(
    r"(?<![가-힣])필요(?:함|하다|합니다|한|가|는)?(?=$|[^가-힣])"
)
_KOREAN_MANDATORY_PATTERN = re.compile(
    r"(?<![가-힣])(?:반드시|필수(?:임|입니다|인|가|는)?)(?=$|[^가-힣])|"
    r"(?:해야|하여야)\s*(?:함|한다)(?=$|[^가-힣])"
)
_KOREAN_RECOMMENDED_PATTERN = re.compile(
    r"(?<![가-힣])(?:권장|권고)(?:함|하다|합니다|한다|됨|됩니다)?(?=$|[^가-힣])"
)
_KOREAN_POSSIBLE_PATTERN = re.compile(
    r"(?<![가-힣])가능(?:함|하다|합니다|한)?(?=$|[^가-힣])|"
    r"할\s*수\s*있음(?=$|[^가-힣])"
)
_KOREAN_IMPOSSIBLE_PATTERN = re.compile(
    r"(?<![가-힣])불가(?:함|하다|합니다|한)?(?=$|[^가-힣])|"
    r"할\s*수\s*없음(?=$|[^가-힣])"
)
_KOREAN_CONDITION_PATTERN = re.compile(
    r"(?<![가-힣])경우(?:에|에는|가|엔)?(?=$|[^가-힣])"
)
_KOREAN_RECOMMENDED_TARGET = re.compile(
    r"\b(?:nên|khuyến nghị|khuyến cáo)\b", re.IGNORECASE
)
_KOREAN_SEMANTIC_CUES = (
    (
        "need",
        _KOREAN_NEED_PATTERN,
        re.compile(r"\b(?:cần|cần thiết|yêu cầu)\b", re.IGNORECASE),
    ),
    (
        "mandatory",
        _KOREAN_MANDATORY_PATTERN,
        re.compile(r"\b(?:phải|bắt buộc|nhất thiết)\b", re.IGNORECASE),
    ),
    (
        "recommended",
        _KOREAN_RECOMMENDED_PATTERN,
        _KOREAN_RECOMMENDED_TARGET,
    ),
    (
        "prohibition",
        re.compile(
            r"(?<![가-힣])금지(?:됨|입니다|함)?(?=$|[^가-힣])|"
            r"하지\s*(?:마십시오|말\s*것)"
        ),
        re.compile(r"\b(?:không|cấm|đừng)\b", re.IGNORECASE),
    ),
    (
        "possible",
        _KOREAN_POSSIBLE_PATTERN,
        re.compile(r"\b(?:có thể|được phép|khả thi)\b", re.IGNORECASE),
    ),
    (
        "impossible",
        _KOREAN_IMPOSSIBLE_PATTERN,
        re.compile(r"\b(?:không thể|không được phép|bất khả thi)\b", re.IGNORECASE),
    ),
    (
        "condition",
        _KOREAN_CONDITION_PATTERN,
        re.compile(r"\b(?:nếu|khi|trong trường hợp)\b", re.IGNORECASE),
    ),
    (
        "first",
        re.compile(r"(?<![가-힣])먼저(?![가-힣])"),
        re.compile(r"\b(?:trước tiên|đầu tiên|trước)\b", re.IGNORECASE),
    ),
)
_KOREAN_AFFIRMATIVE_CUES = frozenset({"need", "mandatory", "recommended", "possible"})
_VIETNAMESE_NEGATION_PREFIX = re.compile(
    r"\bkhông(?:\s+\w+){0,2}\s*$", re.IGNORECASE
)
_KOREAN_TEMPORAL_PATTERN = re.compile(r"(?<![가-힣])(이전|이후|전|후)(?![가-힣])")
_KOREAN_ACTION_ANCHOR_PATTERN = re.compile(
    r"검수|시운전|완료|검사|확인|작성|설정|변경|운전|가동|누르|시작|종료|복귀|점검"
)
_KOREAN_ALWAYS_PROTECTED_ENGLISH_TERMS = (
    "Servo Motor",
    "SCARA Robot",
    "Interlock",
)
_KOREAN_CONTEXTUAL_ENGLISH_TERMS = (
    ("Manual", re.compile(r"검수|작성|문서|항목|내용|준비|참조|확인")),
    ("Utility", re.compile(r"공사|설비|장비|공장|배관|전원|공급|연결|시공|현장")),
    ("Qualification", re.compile(r"설비|장비|공정|수행|검증|인증|평가")),
)
_KOREAN_QUOTED_UI_PATTERN = re.compile(
    r'["“]\s*([A-Za-z][A-Za-z0-9 _./:&()+-]{0,79}?)\s*["”]'
)
_KOREAN_UI_BEFORE_ROLE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"([A-Z][A-Za-z0-9]*(?:[ \t]+[A-Z][A-Za-z0-9]*){0,4})"
    r"(?=\s*(?:버튼|메뉴|패널|창|화면|체크박스|모드|항목))"
)
_KOREAN_UI_ROLE_LABEL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"([A-Z][A-Za-z0-9]*(?:[ \t]+[A-Z][A-Za-z0-9]*){0,3}[ \t]+"
    r"(?:Panel|Window|Button|Menu|Mode|Option|Data))"
    r'(?=\s*(?:[)\]"”]|[가-힣]|$))'
)
_KOREAN_LEADING_UI_PATTERN = re.compile(
    r"^\s*(?:[-•■□▪]\s*)?"
    r"([A-Z][A-Za-z0-9]*(?:[ \t]+[A-Z][A-Za-z0-9]*){0,4})\s*:"
)
_KOREAN_ACRONYM_PATTERN = re.compile(r"(?<!\w)[A-Z]{2,8}(?:/[A-Z0-9]{1,8})?(?!\w)")
_KOREAN_RANGE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.])[-+]?(?:\d+(?:\.\d+)?)\s*(?:±|–|-|~|～)\s*"
    r"[-+]?(?:\d+(?:\.\d+)?)(?![A-Za-z0-9_]|\.\d)"
)


def contains_hangul(text: str) -> bool:
    return _HANGUL_PATTERN.search(TAG_PATTERN.sub("", text)) is not None


def is_context_sensitive_korean(text: str) -> bool:
    """Identify the narrow Korean subset that must not share source-only context."""
    visible = TAG_PATTERN.sub("", text)
    if not contains_hangul(visible):
        return False
    english_remainder = TECHNICAL_IDENTIFIER_PATTERN.sub("", visible)
    english_remainder = POLARITY_PATTERN.sub("", english_remainder)
    return bool(
        _KOREAN_AMBIGUOUS_TERM_PATTERN.search(visible)
        or _ASCII_WORD_PATTERN.search(english_remainder)
    )


def _literal_ascii_count(text: str, term: str) -> int:
    return len(re.findall(rf"(?<!\w){re.escape(term)}(?!\w)", text))


def protected_korean_ui_labels(
    text: str,
    terminology: Mapping[str, str] | None = None,
) -> tuple[ProtectedLiteralSpan, ...]:
    """Return English labels whose Korean syntax identifies them as UI text.

    Quotation, a UI-role noun, or a leading ``Label:`` form is required.  This
    avoids freezing arbitrary Title Case English embedded in ordinary prose.
    Explicit terminology entries keep precedence and therefore are omitted.
    """
    if not contains_hangul(text):
        return ()
    overrides = terminology or {}
    candidates: list[ProtectedLiteralSpan] = []
    patterns = (
        _KOREAN_QUOTED_UI_PATTERN,
        _KOREAN_UI_BEFORE_ROLE_PATTERN,
        _KOREAN_UI_ROLE_LABEL_PATTERN,
        _KOREAN_LEADING_UI_PATTERN,
    )
    for pattern in patterns:
        for match in pattern.finditer(text):
            literal = match.group(1).strip()
            if not literal or literal in overrides:
                continue
            relative = match.group(0).find(match.group(1))
            start = match.start() + relative
            while start < match.end() and text[start].isspace():
                start += 1
            end = start + len(literal)
            candidates.append(ProtectedLiteralSpan(start, end, literal, "Korean UI label"))
    candidates.sort(key=lambda span: (span.start, -(span.end - span.start)))
    selected: list[ProtectedLiteralSpan] = []
    cursor = -1
    for candidate in candidates:
        if candidate.start < cursor:
            continue
        selected.append(candidate)
        cursor = candidate.end
    return tuple(selected)


def validate_korean_english_spans(
    source: str,
    translated: str,
    terminology: Mapping[str, str] | None = None,
) -> None:
    """Preserve a small reviewed set of English terms and explicit Korean UI labels."""
    if not contains_hangul(source):
        return
    terminology = terminology or {}
    protected: list[str] = []
    for term in _KOREAN_ALWAYS_PROTECTED_ENGLISH_TERMS:
        if term not in terminology and _literal_ascii_count(source, term):
            protected.extend([term] * _literal_ascii_count(source, term))
    for term, context_pattern in _KOREAN_CONTEXTUAL_ENGLISH_TERMS:
        if (
            term not in terminology
            and context_pattern.search(source)
            and _literal_ascii_count(source, term)
        ):
            protected.extend([term] * _literal_ascii_count(source, term))
    protected.extend(span.literal for span in protected_korean_ui_labels(source, terminology))
    protected.extend(match[0] for match in _KOREAN_ACRONYM_PATTERN.finditer(source))
    required = Counter(protected)
    missing = tuple(
        term
        for term, count in required.items()
        for _ in range(max(0, count - _literal_ascii_count(translated, term)))
    )
    if missing:
        raise TechnicalInvariantError(
            [InvariantDifference("Korean technical English spans", missing, ())]
        )


def _korean_temporal_relation(text: str) -> str | None:
    cleaned = TAG_PATTERN.sub("", text)
    matches = list(_KOREAN_TEMPORAL_PATTERN.finditer(cleaned))
    if len(matches) != 1:
        return None
    match = matches[0]
    left, right = cleaned[:match.start()], cleaned[match.end():]

    def has_anchor(value: str) -> bool:
        return bool(
            _KOREAN_ACTION_ANCHOR_PATTERN.search(value)
            or TECHNICAL_IDENTIFIER_PATTERN.search(value)
            or POLARITY_PATTERN.search(value)
        )

    if not left.strip() or not right.strip() or not has_anchor(left) or not has_anchor(right):
        return None
    return "before" if match[1] in {"전", "이전"} else "after"


def _vietnamese_temporal_relation(text: str) -> str | None:
    matches = list(_ORDER_PATTERN.finditer(text))
    if len(matches) != 1:
        return None
    return "before" if matches[0][0].casefold().startswith("trước") else "after"


def _canonical_korean_ranges(text: str) -> list[str]:
    return [re.sub(r"\s*(?:–|-|~|～)\s*", "–", match[0]) for match in _KOREAN_RANGE_PATTERN.finditer(text)]


def _has_korean_target_cue(name: str, pattern: re.Pattern[str], text: str) -> bool:
    """Require positive Korean modalities to remain positive in Vietnamese."""
    for match in pattern.finditer(text):
        if name not in _KOREAN_AFFIRMATIVE_CUES:
            return True
        prefix = text[max(0, match.start() - 40):match.start()]
        if not _VIETNAMESE_NEGATION_PREFIX.search(prefix):
            return True
    return False


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
    korean_source = contains_hangul(source)
    differences = [] if korean_source else [
        InvariantDifference(f"semantic {name}", (name,), ())
        for name, source_pattern, target_pattern in _COMPILED_SEMANTIC_CUES
        if source_pattern.search(source) and not target_pattern.search(translated)
    ]
    if korean_source:
        differences.extend(
            InvariantDifference(f"semantic Korean {name}", (name,), ())
            for name, source_pattern, target_pattern in _KOREAN_SEMANTIC_CUES
            if source_pattern.search(source)
            and not _has_korean_target_cue(name, target_pattern, translated)
        )
        source_relation = _korean_temporal_relation(source)
        if source_relation and _vietnamese_temporal_relation(translated) != source_relation:
            differences.append(
                InvariantDifference("semantic Korean order", (source_relation,), ())
            )
        if (
            re.search(r"대응.*(?<!불)필요", source)
            and not re.search(r"담당|책임", source)
            and re.search(r"chịu\s+trách\s+nhiệm|người\s+phụ\s+trách|trách\s+nhiệm", translated, re.IGNORECASE)
        ):
            differences.append(
                InvariantDifference("semantic Korean unsupported responsibility", (), ("responsibility",))
            )
    source_order, target_order = _anchored_order(source), _anchored_order(translated)
    if source_order and target_order and source_order != target_order:
        differences.append(InvariantDifference("semantic order", source_order, target_order))
    if differences:
        raise TechnicalInvariantError(differences)


def validate_technical_invariants(source: str, translated: str) -> None:
    """Reject changed, removed, duplicated, or invented deterministic tokens."""
    differences: list[InvariantDifference] = _association_differences(source, translated)
    for category, extractor in TOKEN_CHECKS:
        if category == "engineering ranges" and contains_hangul(source):
            source_tokens = Counter(_canonical_korean_ranges(source))
            translated_tokens = Counter(_canonical_korean_ranges(translated))
        else:
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
