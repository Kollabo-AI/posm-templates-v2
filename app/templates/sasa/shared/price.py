from __future__ import annotations

import enum
import re
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

from .... import logger
from ....renderer import (
    BoundingBox,
    Empty,
    Group,
    LayoutElement,
    TextLine,
    TextSpan,
    TextStyle,
    VerticalAlign,
)


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


class PriceSpanStyle(BaseModel):
    text_style: TextStyle
    gap_before: float = 2
    gap_after: float = 2
    align_to_previous: VerticalAlign | None = None
    align_to_next: VerticalAlign | None = None

    model_config = ConfigDict(frozen=True, extra="forbid")


class PriceLineTextStyle(BaseModel):
    currency: PriceSpanStyle
    price: PriceSpanStyle
    suffix: PriceSpanStyle
    note: PriceSpanStyle
    hyphen: PriceSpanStyle | None = None
    plus: PriceSpanStyle | None = None
    star: PriceSpanStyle | None = None
    slash: PriceSpanStyle | None = None
    other: PriceSpanStyle | None = None
    grouped_currency: PriceSpanStyle | None = None
    currency_gap_before_one: float | None = None
    currency_gap_other: float | None = None
    whitespace_gap: float = 0
    line_gap: float = 5

    model_config = ConfigDict(frozen=True, extra="forbid")


def _require_placement_box(element: LayoutElement) -> BoundingBox:
    box = element.placement_box()
    if box is None:
        raise ValueError(f"{type(element).__name__} has no placement box")
    return box


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
_DUPLICATE_DOLLAR_PATTERN = re.compile(r"^(?P<indent>\s*)\${2,}")
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


def split_price_lines(price: str) -> list[str]:
    return [line.strip() for line in price.splitlines() if line.strip()]


def _collapse_duplicate_leading_dollars(line: str) -> str:
    match = _DUPLICATE_DOLLAR_PATTERN.match(line)
    if match is None:
        return line
    logger.warning(
        f"Line starts with repeated dollar signs. Please check the input: {line}"
    )
    return f"{match.group('indent')}${line[match.end():]}"


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


def _next_non_whitespace_token(
    tokens: list[tuple[str, TokenType]],
    index: int,
) -> tuple[str, TokenType] | None:
    for token in tokens[index + 1:]:
        if token[1] != TokenType.WHITESPACE:
            return token
    return None


def _append_token(
    tokens: list[tuple[str, TokenType]],
    text: str,
    token_type: TokenType,
) -> None:
    mergeable_types = {
        TokenType.SUFFIX,
        TokenType.OTHER,
        TokenType.WHITESPACE,
    }
    if tokens and token_type in mergeable_types and tokens[-1][1] == token_type:
        previous_text, _ = tokens[-1]
        tokens[-1] = (previous_text + text, token_type)
        return
    tokens.append((text, token_type))


def tokenize_price_line(
    line: str,
    first_line: bool = True,
) -> list[tuple[str, TokenType]]:
    """Split one price line into semantic, lossless tokens.

    ``first_line`` is retained for compatibility. Note detection is content-based
    so a price-bearing later line is still tokenized normally.
    """
    if not line.strip():
        return []

    normalized_line = _collapse_duplicate_leading_dollars(line)
    raw_tokens = _scan_price_line(normalized_line)
    if _is_note_line(normalized_line, raw_tokens):
        return [(normalized_line, TokenType.NOTE)]

    tokens: list[tuple[str, TokenType]] = []
    suffix_mode = False
    for index, (text, token_type) in enumerate(raw_tokens):
        if token_type == TokenType.SLASH:
            next_token = _next_non_whitespace_token(raw_tokens, index)
            if next_token is not None and next_token[1] == TokenType.CURRENCY:
                suffix_mode = False
                _append_token(tokens, text, TokenType.SLASH)
            else:
                suffix_mode = True
                _append_token(tokens, text, TokenType.SUFFIX)
            continue

        if token_type == TokenType.CURRENCY:
            suffix_mode = False
            _append_token(tokens, text, token_type)
        elif token_type == TokenType.PRICE:
            _append_token(
                tokens,
                text,
                TokenType.SUFFIX if suffix_mode else TokenType.PRICE,
            )
        elif token_type == TokenType.OTHER:
            _append_token(
                tokens,
                text,
                TokenType.SUFFIX if suffix_mode or text.isalnum() else TokenType.OTHER,
            )
        else:
            _append_token(tokens, text, token_type)

    return tokens


def _next_rendered_token_index(
    tokens: list[tuple[str, TokenType]],
    index: int,
) -> int | None:
    for next_index in range(index + 1, len(tokens)):
        if tokens[next_index][1] != TokenType.WHITESPACE:
            return next_index
    return None


def _price_span_style(
    tokens: list[tuple[str, TokenType]],
    index: int,
    style: PriceLineTextStyle,
) -> PriceSpanStyle:
    _, token_type = tokens[index]
    if token_type == TokenType.CURRENCY:
        next_index = _next_rendered_token_index(tokens, index)
        if (
            style.grouped_currency is not None
            and next_index is not None
            and tokens[next_index][1] == TokenType.PRICE
            and "," in tokens[next_index][0]
        ):
            return style.grouped_currency
        return style.currency
    if token_type == TokenType.PRICE:
        return style.price
    if token_type == TokenType.NOTE:
        return style.note
    if token_type == TokenType.HYPHEN:
        return style.hyphen or style.suffix
    if token_type == TokenType.PLUS:
        return style.plus or style.suffix
    if token_type == TokenType.STAR:
        return style.star or style.suffix
    if token_type == TokenType.SLASH:
        return style.slash or style.suffix
    if token_type == TokenType.OTHER:
        return style.other or style.suffix
    return style.suffix


def price_line_spans(
    line: str,
    style: PriceLineTextStyle,
) -> list[TextSpan]:
    tokens = tokenize_price_line(line)
    rendered_indices = [
        index
        for index, (_, token_type) in enumerate(tokens)
        if token_type != TokenType.WHITESPACE
    ]
    span_styles = [
        _price_span_style(tokens, token_index, style)
        for token_index in rendered_indices
    ]
    align_to_previous: list[VerticalAlign | None] = []
    align_to_next: list[VerticalAlign | None] = []
    for index, span_style in enumerate(span_styles):
        previous_alignment = (
            span_style.align_to_previous if index > 0 else None
        )
        next_alignment = (
            span_style.align_to_next if index + 1 < len(span_styles) else None
        )
        if previous_alignment is not None and next_alignment is not None:
            next_alignment = None
        align_to_previous.append(previous_alignment)
        align_to_next.append(next_alignment)

    for index in range(len(span_styles) - 1):
        if (
            align_to_next[index] is not None
            and align_to_previous[index + 1] is not None
        ):
            align_to_next[index] = None

    spans: list[TextSpan] = []

    for rendered_index, token_index in enumerate(rendered_indices):
        token, token_type = tokens[token_index]
        span_style = span_styles[rendered_index]
        next_token_index = (
            rendered_indices[rendered_index + 1]
            if rendered_index + 1 < len(rendered_indices)
            else None
        )
        gap_after = span_style.gap_after
        if next_token_index is not None:
            next_span_style = span_styles[rendered_index + 1]
            gap_after = max(gap_after, next_span_style.gap_before)
            whitespace = "".join(
                text
                for text, whitespace_type in tokens[token_index + 1:next_token_index]
                if whitespace_type == TokenType.WHITESPACE
            )

            next_token, next_token_type = tokens[next_token_index]
            if token_type == TokenType.CURRENCY:
                currency_gap = style.currency_gap_other
                if (
                    next_token_type == TokenType.PRICE
                    and next_token.startswith("1")
                ):
                    currency_gap = style.currency_gap_before_one
                if currency_gap is not None:
                    gap_after = currency_gap
            gap_after += len(whitespace.expandtabs()) * style.whitespace_gap

        spans.append(
            TextSpan(
                text=token,
                style=span_style.text_style,
                gap_after=gap_after,
                align_to_previous=align_to_previous[rendered_index],
                align_to_next=align_to_next[rendered_index],
            )
        )

    return spans


def _price_line_height(spans: list[TextSpan]) -> float:
    font_sizes = [span.style.font_size for span in spans if span.style.font_size is not None]
    if not font_sizes:
        raise ValueError("Price line styles must define at least one font size")
    return max(font_sizes)


def _estimated_price_line_width(spans: list[TextSpan], line_height: float) -> float:
    return sum(
        max(1, len(span.text)) * (span.style.font_size or line_height)
        + span.gap_after
        for span in spans
    )


def render_price_line(
    line: str,
    box: BoundingBox,
    style: PriceLineTextStyle,
    *,
    expand_to_fit_content: bool = False,
    tight: bool = True,
) -> LayoutElement:
    spans = price_line_spans(line, style)
    if not spans:
        return Empty()

    line_height = _price_line_height(spans)
    width = box.w
    if expand_to_fit_content:
        width = max(width, _estimated_price_line_width(spans, line_height))
    return TextLine(
        spans=spans,
        box=BoundingBox(l=box.l, t=box.t, w=width, h=line_height),
        tight=tight,
    )


def render_price_lines(
    price: str,
    box: BoundingBox,
    style: PriceLineTextStyle,
    *,
    expand_to_fit_content: bool = False,
    transform_line: Callable[[LayoutElement], LayoutElement] | None = None,
    tight: bool = True,
) -> LayoutElement:
    lines = split_price_lines(price)
    if not lines:
        return Empty()

    elements: list[LayoutElement] = []
    current_top = box.t
    for _, line in enumerate(lines):
        line_element = render_price_line(
            line,
            BoundingBox(l=box.l, t=current_top, w=box.w, h=box.h),
            style,
            expand_to_fit_content=expand_to_fit_content,
            tight=tight,
        )
        if transform_line is not None:
            line_element = transform_line(line_element)
        elements.append(line_element)
        line_box = _require_placement_box(line_element)
        current_top = line_box.b + style.line_gap

    return Group(elements=elements)
