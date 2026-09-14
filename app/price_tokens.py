from __future__ import annotations

import enum
import re

class TokenType(enum.Enum):
    CURRENCY = 1
    PRICE = 2
    SUFFIX = 3
    HYPHEN = 4
    PLUS = 5
    STAR = 6
    NOTE = 7
    OTHER = 8
    SLASH = 9
    WHITESPACE = 10


_TOKEN_PATTERN = re.compile(
    r"(?P<currency>MOP|\$)"
    r"|(?P<price>\d[\d,]*(?:\.\d+)?)"
    r"|(?P<hyphen>-)"
    r"|(?P<plus>\+)"
    r"|(?P<star>[*#])"
    r"|(?P<slash>/)"
    r"|(?P<whitespace>\s+)"
    r"|(?P<other>.)",
    re.IGNORECASE | re.DOTALL,
)


_NOTE_MARKER_PATTERN = re.compile(r"每件|平均")


_BARE_PRICE_LINE_PATTERN = re.compile(
    r"^\s*\+?\s*\d[\d,]*(?:\.\d+)?"
    r"(?:\s*-\s*\d[\d,]*(?:\.\d+)?)?"
    r"(?:\s*/\s*(?:\d+\s*)?[\u3400-\u4DBF\u4E00-\u9FFF]+)?"
    r"(?:\s*[*#]\s*\S*)?\s*$"
)


_RAW_TOKEN_TYPES = {
    "currency": TokenType.CURRENCY,
    "price": TokenType.PRICE,
    "hyphen": TokenType.HYPHEN,
    "plus": TokenType.PLUS,
    "star": TokenType.STAR,
    "slash": TokenType.SLASH,
    "whitespace": TokenType.WHITESPACE,
    "other": TokenType.OTHER,
}


def _scan_price_line(line: str) -> list[tuple[str, TokenType]]:
    tokens: list[tuple[str, TokenType]] = []
    for match in _TOKEN_PATTERN.finditer(line):
        group_name = match.lastgroup
        if group_name is None:
            raise RuntimeError(f"Could not classify price token: {match.group()}")
        tokens.append((match.group(), _RAW_TOKEN_TYPES[group_name]))
    return tokens


def _is_note_line(line: str, tokens: list[tuple[str, TokenType]]) -> bool:
    stripped = line.strip()
    has_price = any(token_type == TokenType.PRICE for _, token_type in tokens)
    has_currency = any(
        token_type == TokenType.CURRENCY for _, token_type in tokens
    )
    return (
        (stripped.startswith("(") and stripped.endswith(")"))
        or _NOTE_MARKER_PATTERN.search(stripped) is not None
        or not has_price
        or (
            not has_currency
            and _BARE_PRICE_LINE_PATTERN.fullmatch(stripped) is None
        )
    )


def is_price_line(line: str) -> bool:
    if not line.strip():
        return False
    tokens = _scan_price_line(line)
    return not _is_note_line(line, tokens)
