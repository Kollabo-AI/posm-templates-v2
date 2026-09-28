from __future__ import annotations

import re
from dataclasses import dataclass


_CJK_RE = re.compile(
    r"[\u3400-\u4DBF"
    r"\u4E00-\u9FFF"
    r"\U00020000-\U0002A6DF"
    r"\U0002A700-\U0002B73F"
    r"\U0002B740-\U0002B81F"
    r"\U0002B820-\U0002CEAF"
    r"\U0002CEB0-\U0002EBEF]"
)


@dataclass(frozen=True)
class _BreakCandidate:
    pos: int
    cost: float
    reason: str


def split_text(text: str, max_lines: int) -> str:
    return "\n".join(_split_into_at_most_n_lines(text, max_lines))


def _split_into_at_most_n_lines(
    text: str,
    max_lines: int,
    break_cost_weight: float = 20.0,
    min_line_chars: int = 1,
) -> list[str]:
    cleaned_text = text.strip()

    if not cleaned_text:
        return []

    if max_lines <= 1:
        return [cleaned_text]

    for line_count in range(max_lines, 0, -1):
        lines = _split_exactly_n_lines(
            cleaned_text,
            line_count,
            break_cost_weight=break_cost_weight,
            min_line_chars=min_line_chars,
        )
        if lines is not None:
            return lines

    return [cleaned_text]


def _split_exactly_n_lines(
    text: str,
    line_count: int,
    break_cost_weight: float = 20.0,
    min_line_chars: int = 1,
) -> list[str] | None:
    if line_count <= 0:
        return None

    cleaned_text = text.strip()

    if not cleaned_text:
        return []

    if line_count == 1:
        return [cleaned_text]

    text_length = len(cleaned_text)
    candidates = _collect_break_candidates(cleaned_text)

    if len(candidates) < line_count - 1:
        return None

    cost_by_pos = {candidate.pos: candidate.cost for candidate in candidates}
    positions = sorted({0, text_length, *(candidate.pos for candidate in candidates)})
    pos_to_index = {pos: index for index, pos in enumerate(positions)}
    target_line_length = text_length / line_count

    position_count = len(positions)
    start_index = pos_to_index[0]
    end_index = pos_to_index[text_length]
    dp: list[list[float]] = [
        [float("inf")] * position_count for _ in range(line_count + 1)
    ]
    parent: list[list[int | None]] = [
        [None] * position_count for _ in range(line_count + 1)
    ]

    dp[0][start_index] = 0.0

    for current_line in range(1, line_count + 1):
        for end_pos_index in range(1, position_count):
            end_pos = positions[end_pos_index]

            for start_pos_index in range(end_pos_index):
                if dp[current_line - 1][start_pos_index] == float("inf"):
                    continue

                start_pos = positions[start_pos_index]
                segment = cleaned_text[start_pos:end_pos].strip()
                if len(segment) < min_line_chars:
                    continue

                break_cost = 0.0
                if end_pos != text_length:
                    break_cost = cost_by_pos.get(end_pos, float("inf"))
                    if break_cost == float("inf"):
                        continue

                length_error = len(segment) - target_line_length
                score = (
                    dp[current_line - 1][start_pos_index]
                    + length_error * length_error
                    + break_cost_weight * break_cost
                )

                if score < dp[current_line][end_pos_index]:
                    dp[current_line][end_pos_index] = score
                    parent[current_line][end_pos_index] = start_pos_index

    if dp[line_count][end_index] == float("inf"):
        return None

    boundaries = _reconstruct_boundaries(
        parent=parent,
        positions=positions,
        line_count=line_count,
        end_index=end_index,
    )
    if boundaries is None:
        return None

    lines = _split_by_boundaries(cleaned_text, boundaries)
    if len(lines) != line_count:
        return None

    return lines


def _collect_break_candidates(text: str) -> list[_BreakCandidate]:
    candidates: dict[int, _BreakCandidate] = {}

    def add(pos: int, cost: float, reason: str) -> None:
        if pos <= 0 or pos >= len(text):
            return

        left = text[pos - 1]
        right = text[pos]
        if _is_forbidden_alnum_boundary(left, right):
            return

        if _is_inside_numeric_expression(text, pos):
            return

        old_candidate = candidates.get(pos)
        if old_candidate is None or cost < old_candidate.cost:
            candidates[pos] = _BreakCandidate(pos=pos, cost=cost, reason=reason)

    strong_punctuation = set("。！？!?；;")
    medium_punctuation = set("，,、：:")
    weak_punctuation = set("·•")
    opening = set("([{（【《〈「『“‘")
    closing = set(")]}）】》〉」』”’")
    separators = set("/\\|+-–—=")

    for index, char in enumerate(text):
        if char in opening:
            add(index, 0.8, "before opening bracket")

        if char in strong_punctuation:
            add(index + 1, 0.1, "after strong punctuation")

        if char in medium_punctuation:
            add(index + 1, 0.3, "after medium punctuation")

        if char in weak_punctuation:
            add(index + 1, 1.2, "after weak punctuation")

        if char in closing:
            add(index + 1, 0.7, "after closing bracket")

        if char.isspace():
            add(index + 1, 0.2, "after whitespace")

        if index + 1 >= len(text):
            continue

        next_char = text[index + 1]

        if _is_cjk(char) and _is_cjk(next_char):
            add(index + 1, 1.0, "between CJK characters")

        if _is_cjk(char) and _is_ascii_alnum(next_char):
            add(index + 1, 1.1, "between CJK and ASCII alnum")

        if _is_ascii_alnum(char) and _is_cjk(next_char):
            add(index + 1, 1.1, "between ASCII alnum and CJK")

        if char in separators:
            add(index + 1, 1.5, "after separator")

    return sorted(candidates.values(), key=lambda candidate: candidate.pos)


def _is_cjk(char: str) -> bool:
    return bool(_CJK_RE.match(char))


def _is_ascii_alnum(char: str) -> bool:
    return char.isascii() and char.isalnum()


def _is_ascii_letter(char: str) -> bool:
    return char.isascii() and char.isalpha()


def _is_ascii_digit(char: str) -> bool:
    return char.isascii() and char.isdigit()


def _is_forbidden_alnum_boundary(left: str, right: str) -> bool:
    return (
        _is_ascii_letter(left)
        and _is_ascii_digit(right)
        or _is_ascii_digit(left)
        and _is_ascii_letter(right)
    )


def _is_inside_numeric_expression(text: str, pos: int) -> bool:
    if pos <= 0 or pos >= len(text):
        return False

    left = text[pos - 1]
    right = text[pos]

    if left.isdigit() and right.isdigit():
        return True

    numeric_punctuation = ".,:-/年/月日"

    if pos >= 2:
        before_left = text[pos - 2]
        if before_left.isdigit() and left in numeric_punctuation and right.isdigit():
            return True

    if pos + 1 < len(text):
        after_right = text[pos + 1]
        if left.isdigit() and right in numeric_punctuation and after_right.isdigit():
            return True

    return False


def _reconstruct_boundaries(
    parent: list[list[int | None]],
    positions: list[int],
    line_count: int,
    end_index: int,
) -> list[int] | None:
    boundaries: list[int] = []
    current_line = line_count
    current_index = end_index

    while current_line > 0:
        boundaries.append(positions[current_index])
        previous_index = parent[current_line][current_index]
        if previous_index is None:
            return None
        current_index = previous_index
        current_line -= 1

    boundaries.reverse()
    return boundaries


def _split_by_boundaries(text: str, boundaries: list[int]) -> list[str]:
    lines: list[str] = []
    start = 0

    for end in boundaries:
        line = text[start:end].strip()
        if line:
            lines.append(line)
        start = end

    return lines
