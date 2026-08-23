import json
from dataclasses import dataclass
from html import escape
from typing import Iterable

from django.utils.safestring import SafeString, mark_safe
from markdown_it import MarkdownIt

from .models import HistoryMessage

_MARKDOWN = MarkdownIt(
    "commonmark",
    {"html": False, "breaks": True, "linkify": False, "typographer": False},
).enable(["table", "strikethrough"])


def _render_untrusted_link(tokens, index, options, env):
    tokens[index].attrSet("rel", "nofollow noreferrer")
    return _MARKDOWN.renderer.renderToken(tokens, index, options, env)


def _render_image_placeholder(tokens, index, options, env):
    del options, env
    alt_text = escape(tokens[index].content)
    return f'<span class="markdown-image-placeholder">[图片：{alt_text}]</span>'


_MARKDOWN.renderer.rules["link_open"] = _render_untrusted_link
_MARKDOWN.renderer.rules["image"] = _render_image_placeholder

_ROLE_LABELS = {
    "user": "用户",
    "assistant": "Agent",
    "tool": "工具",
    "system": "系统",
    "developer": "开发者",
    "event": "后台委托完成",
    "context": "会话上下文",
}

_ASYNC_DELEGATION_PREFIXES = (
    "[ASYNC DELEGATION BATCH COMPLETE — ",
    "[ASYNC DELEGATION COMPLETE — ",
)
_ASYNC_DELEGATION_DISPLAY_KIND = "async_delegation_complete"
_CONTEXT_COMPACTION_PREFIXES = (
    "[CONTEXT COMPACTION",
    "[CONTEXT SUMMARY]:",
)


@dataclass(frozen=True)
class ParsedMemoryArguments:
    values: dict
    valid: bool


@dataclass(frozen=True)
class PresentedMessage:
    message: HistoryMessage
    normalized_role: str
    role_label: str
    content_html: SafeString
    is_control_event: bool
    is_context_artifact: bool
    is_memory_tool: bool
    is_hidden: bool
    display_tool_calls: tuple[dict, ...]
    memory_action: str
    memory_content_html: SafeString | None
    memory_arguments_valid: bool
    display_suffix: str = ""

    @property
    def anchor_id(self) -> str:
        suffix = f"-{self.display_suffix}" if self.display_suffix else ""
        return f"message-{self.message.pk}{suffix}"

    @property
    def is_tool(self) -> bool:
        return self.normalized_role == "tool"


@dataclass(frozen=True)
class PresentedTurn:
    number: int
    messages: tuple[PresentedMessage, ...]
    context_messages: tuple[PresentedMessage, ...]
    is_complete: bool


@dataclass(frozen=True)
class HistoryPresentation:
    preamble: tuple[PresentedMessage, ...]
    preamble_memory_tools: tuple[PresentedMessage, ...]
    turns: tuple[PresentedTurn, ...]


def build_history_presentation(messages: Iterable[HistoryMessage]) -> HistoryPresentation:
    preamble: list[PresentedMessage] = []
    preamble_memory_tools: list[PresentedMessage] = []
    turns: list[PresentedTurn] = []
    current: list[PresentedMessage] | None = None

    for message in messages:
        for presented in _presented_messages(message):
            if presented.is_hidden:
                if current is not None:
                    current.append(presented)
                continue
            if presented.is_control_event or presented.is_context_artifact:
                if current is None:
                    if presented.is_memory_tool:
                        preamble_memory_tools.append(presented)
                    else:
                        preamble.append(presented)
                else:
                    current.append(presented)
            elif presented.normalized_role == "user":
                if current is not None:
                    turns.append(_build_turn(len(turns) + 1, current))
                current = [presented]
            elif current is None:
                if presented.is_memory_tool:
                    preamble_memory_tools.append(presented)
                else:
                    preamble.append(presented)
            else:
                current.append(presented)

    if current is not None:
        turns.append(_build_turn(len(turns) + 1, current))

    return HistoryPresentation(
        preamble=tuple(preamble),
        preamble_memory_tools=tuple(preamble_memory_tools),
        turns=tuple(turns),
    )


def _presented_messages(message: HistoryMessage) -> tuple[PresentedMessage, ...]:
    role = message.role.strip().lower()
    normalized_role = (
        "event"
        if is_hermes_control_event(message)
        else "context"
        if is_hermes_context_artifact(message)
        else role or "unknown"
    )
    memory_arguments = extract_memory_arguments(message)
    if role == "tool" and message.tool_name.strip().lower() == "memory":
        return (
            _make_presented_message(
                message,
                normalized_role,
                is_hidden=True,
            ),
        )
    if memory_arguments is not None:
        memory_message = _make_presented_message(
            message,
            normalized_role,
            is_memory_tool=True,
            memory_arguments=memory_arguments,
            display_tool_calls=(),
            display_suffix="memory",
        )
        regular_calls = tuple(_non_memory_tool_calls(message))
        if regular_calls:
            return (
                memory_message,
                _make_presented_message(
                    message,
                    normalized_role,
                    display_tool_calls=regular_calls,
                    display_suffix="tools",
                ),
            )
        return (memory_message,)
    return (_make_presented_message(message, normalized_role),)


def _make_presented_message(
    message: HistoryMessage,
    normalized_role: str,
    *,
    is_memory_tool: bool = False,
    is_hidden: bool = False,
    memory_arguments: ParsedMemoryArguments | None = None,
    display_tool_calls: tuple[dict, ...] | None = None,
    display_suffix: str = "",
) -> PresentedMessage:
    memory_action = ""
    if memory_arguments is not None:
        memory_action = (
            "参数无效"
            if not memory_arguments.valid
            else str(memory_arguments.values.get("action") or "未知")
        )
    return PresentedMessage(
        message=message,
        normalized_role=normalized_role,
        role_label=_ROLE_LABELS.get(normalized_role, message.role or "未知"),
        content_html=render_message_markdown(message.content),
        is_control_event=normalized_role == "event",
        is_context_artifact=normalized_role == "context",
        is_memory_tool=is_memory_tool,
        is_hidden=is_hidden,
        display_tool_calls=(
            display_tool_calls
            if display_tool_calls is not None
            else tuple(_json_tool_calls(message.tool_calls))
        ),
        memory_action=memory_action,
        memory_content_html=(
            render_message_markdown(_memory_content(memory_arguments.values))
            if memory_arguments is not None and memory_arguments.valid
            else None
        ),
        memory_arguments_valid=memory_arguments is None or memory_arguments.valid,
        display_suffix=display_suffix,
    )


def _json_tool_calls(tool_calls) -> list[dict]:
    if not isinstance(tool_calls, list):
        return []
    return [tool_call for tool_call in tool_calls if isinstance(tool_call, dict)]


def _non_memory_tool_calls(message: HistoryMessage) -> list[dict]:
    return [
        tool_call
        for tool_call in _json_tool_calls(message.tool_calls)
        if _tool_call_name(tool_call).strip().lower() != "memory"
    ]


def _build_turn(number: int, messages: list[PresentedMessage]) -> PresentedTurn:
    visible_messages = []
    context_messages = []
    for message in messages:
        if message.is_hidden:
            continue
        if not visible_messages or not is_iteration_context(message):
            visible_messages.append(message)
        else:
            context_messages.append(message)
    substantive_messages = [
        message
        for message in visible_messages
        if not message.is_control_event and not message.is_context_artifact
    ]
    is_complete = bool(
        substantive_messages and substantive_messages[-1].normalized_role == "assistant"
    )
    return PresentedTurn(
        number=number,
        messages=tuple(visible_messages),
        context_messages=tuple(context_messages),
        is_complete=is_complete,
    )


def is_iteration_context(message: PresentedMessage) -> bool:
    if message.is_memory_tool:
        return False
    if message.is_control_event or message.is_context_artifact:
        return True
    role = message.normalized_role
    if role in {"tool", "system", "developer"}:
        return True
    if role == "assistant":
        return bool(message.message.tool_calls) or not message.message.content.strip()
    return False


def message_uses_memory_tool(message: HistoryMessage) -> bool:
    role = message.role.strip().lower()
    if role == "tool":
        return message.tool_name.strip().lower() == "memory"
    if role != "assistant":
        return False
    return any(
        _tool_call_name(tool_call).strip().lower() == "memory"
        for tool_call in (message.tool_calls or [])
        if isinstance(tool_call, dict)
    )


def extract_memory_arguments(message: HistoryMessage) -> ParsedMemoryArguments | None:
    if message.role.strip().lower() != "assistant":
        return None
    for tool_call in message.tool_calls or []:
        if not isinstance(tool_call, dict):
            continue
        if _tool_call_name(tool_call).strip().lower() != "memory":
            continue
        function = tool_call.get("function")
        arguments = (
            function.get("arguments") if isinstance(function, dict) else tool_call.get("arguments")
        )
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except (TypeError, ValueError):
                return ParsedMemoryArguments({}, False)
        if isinstance(arguments, dict):
            return ParsedMemoryArguments(arguments, True)
        return ParsedMemoryArguments({}, False)
    return None


def _memory_content(arguments: dict) -> str:
    content = arguments.get("content", "")
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content)


def _tool_call_name(tool_call: dict) -> str:
    name = tool_call.get("name")
    if isinstance(name, str):
        return name
    function = tool_call.get("function")
    if isinstance(function, dict) and isinstance(function.get("name"), str):
        return function["name"]
    return ""


def is_hermes_control_event(message: HistoryMessage) -> bool:
    if message.role.strip().lower() != "user":
        return False
    metadata = message.raw_metadata if isinstance(message.raw_metadata, dict) else {}
    display_kind = metadata.get("display_kind")
    if display_kind == _ASYNC_DELEGATION_DISPLAY_KIND:
        return True
    if not isinstance(message.content, str):
        return False
    first_line = message.content.splitlines()[0] if message.content else ""
    return first_line.endswith("]") and first_line.startswith(_ASYNC_DELEGATION_PREFIXES)


def is_hermes_context_artifact(message: HistoryMessage) -> bool:
    if message.role.strip().lower() != "user" or not isinstance(message.content, str):
        return False
    first_line = message.content.splitlines()[0] if message.content else ""
    return first_line.startswith(_CONTEXT_COMPACTION_PREFIXES)


def render_message_markdown(content: str) -> SafeString:
    rendered = _MARKDOWN.render(content).replace("'", "&#x27;")
    return mark_safe(rendered)  # noqa: S308
