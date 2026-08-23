import json
import math
import re
from dataclasses import dataclass
from typing import Iterable

from .models import HistoryMessage

_FENCED_CODE_RE = re.compile(
    r"(?:^|\n)(?P<fence>`{3,}|~{3,})[^\n]*\n(?P<code>.*?)(?:\n(?P=fence)[ \t]*(?=\n|$)|\Z)",
    re.DOTALL,
)
_INLINE_CODE_RE = re.compile(r"(?<!`)`(?P<code>[^`\n]+)`(?!`)")
_CONTROL_PREFIXES = (
    "[ASYNC DELEGATION BATCH COMPLETE",
    "[CONTEXT COMPACTION",
)
_CONTROL_DISPLAY_KINDS = {"async_delegation_complete", "context_compaction"}


@dataclass(frozen=True)
class ContextSegment:
    key: str
    label: str
    tokens: int
    percent: float
    start_percent: float


@dataclass(frozen=True)
class ContextAllocation:
    segments: tuple[ContextSegment, ...]
    total_tokens: int

    @property
    def tokens_by_key(self) -> dict[str, int]:
        return {segment.key: segment.tokens for segment in self.segments}


_CATEGORY_LABELS = (
    ("reasoning", "推理"),
    ("code", "代码"),
    ("conversation", "对话"),
    ("tools", "工具"),
    ("system", "系统 / 控制"),
)


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x9FFF
        or 0x3040 <= codepoint <= 0x30FF
        or 0xAC00 <= codepoint <= 0xD7AF
        or 0xF900 <= codepoint <= 0xFAFF
    )


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk_characters = sum(1 for character in text if _is_cjk(character))
    other_characters = len(text) - cjk_characters
    return cjk_characters + math.ceil(other_characters / 4)


def _json_text(value) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def _reasoning_text(message: HistoryMessage) -> str:
    metadata = message.raw_metadata if isinstance(message.raw_metadata, dict) else {}
    for key in ("reasoning_content", "reasoning", "reasoning_details"):
        text = _json_text(metadata.get(key))
        if text:
            return text
    return ""


def _split_code(text: str) -> tuple[str, str]:
    if not text:
        return "", ""
    code_parts = []
    remaining_parts = []
    position = 0
    for match in _FENCED_CODE_RE.finditer(text):
        remaining_parts.append(text[position : match.start()])
        code_parts.append(match.group("code"))
        position = match.end()
    remaining_parts.append(text[position:])
    remaining = "".join(remaining_parts)

    inline_parts = []

    def remove_inline(match: re.Match) -> str:
        inline_parts.append(match.group("code"))
        return " "

    remaining = _INLINE_CODE_RE.sub(remove_inline, remaining)
    return "\n".join([*code_parts, *inline_parts]), remaining


def _is_control_message(message: HistoryMessage) -> bool:
    role = (message.role or "").strip().lower()
    if role in {"system", "developer"}:
        return True
    metadata = message.raw_metadata if isinstance(message.raw_metadata, dict) else {}
    if metadata.get("display_kind") in _CONTROL_DISPLAY_KINDS:
        return True
    content = message.content.lstrip() if isinstance(message.content, str) else ""
    return content.startswith(_CONTROL_PREFIXES)


def _segments_from_counts(counts: dict[str, int]) -> tuple[ContextSegment, ...]:
    total = sum(counts.values())
    nonempty = [(key, label, counts[key]) for key, label in _CATEGORY_LABELS if counts[key] > 0]
    if not nonempty:
        return ()

    # Allocate tenths of a percent as integers so every non-empty category is
    # visible and rounding can never make the final SVG segment negative.
    distributable_units = 1000 - len(nonempty)
    units = []
    remainders = []
    for index, (_, _, tokens) in enumerate(nonempty):
        quotient, remainder = divmod(tokens * distributable_units, total)
        units.append(1 + quotient)
        remainders.append((remainder, index))
    for _, index in sorted(remainders, key=lambda item: (-item[0], item[1]))[: 1000 - sum(units)]:
        units[index] += 1

    segments = []
    start_units = 0
    for (key, label, tokens), segment_units in zip(nonempty, units, strict=True):
        segments.append(
            ContextSegment(
                key=key,
                label=label,
                tokens=tokens,
                percent=segment_units / 10,
                start_percent=start_units / 10,
            )
        )
        start_units += segment_units
    return tuple(segments)


def build_context_allocation(messages: Iterable[HistoryMessage]) -> ContextAllocation:
    counts = {key: 0 for key, _ in _CATEGORY_LABELS}
    for message in messages:
        counts["reasoning"] += estimate_tokens(_reasoning_text(message))
        role = (message.role or "").strip().lower()
        content = (
            message.content if isinstance(message.content, str) else _json_text(message.content)
        )

        if role == "tool" or message.tool_name:
            counts["tools"] += estimate_tokens(content)
        else:
            code, prose = _split_code(content)
            counts["code"] += estimate_tokens(code)
            bucket = "system" if _is_control_message(message) else "conversation"
            counts[bucket] += estimate_tokens(prose)

        counts["tools"] += estimate_tokens(_json_text(message.tool_calls))

    segments = _segments_from_counts(counts)
    return ContextAllocation(segments=segments, total_tokens=sum(counts.values()))
