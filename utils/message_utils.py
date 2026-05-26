def _has_tool_calls(message) -> bool:
    if isinstance(message, dict):
        return bool(message.get("tool_calls"))
    return bool(getattr(message, "tool_calls", None))


def _message_role(message) -> str:
    if isinstance(message, dict):
        return str(message.get("role", ""))
    return str(getattr(message, "type", ""))


def _tool_calls(message):
    if isinstance(message, dict):
        return message.get("tool_calls") or []
    return getattr(message, "tool_calls", None) or []


def _tool_call_id(message):
    if isinstance(message, dict):
        return message.get("tool_call_id")
    return getattr(message, "tool_call_id", None)


def is_tool_message(message) -> bool:
    role = _message_role(message)
    return role == "tool"


def trailing_tool_batch(messages: list) -> list:
    """
    Tool messages appended together after the last AI tool-call turn (contiguous from the end).
    Used to detect handoff payloads without scanning stale tool outputs from earlier turns.
    """
    if not messages:
        return []
    batch = []
    for i in range(len(messages) - 1, -1, -1):
        if is_tool_message(messages[i]):
            batch.append(messages[i])
        else:
            break
    return list(reversed(batch))


def collect_message_text_blobs(message) -> list[str]:
    """String forms of a message body (for JSON / REJECTED_BY_MEDIC detection)."""
    import json

    out: list[str] = []
    content = getattr(message, "content", None)
    if content is None:
        return out
    if isinstance(content, str):
        out.append(content)
    elif isinstance(content, dict):
        out.append(json.dumps(content, ensure_ascii=False))
        out.append(str(content))
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("text"):
                out.append(str(block["text"]))
            else:
                out.append(str(block))
    else:
        out.append(str(content))
    return out


def safe_recent_messages(messages, limit: int = 8):
    """
    Keep a short context window while preserving only valid tool-call chains.
    OpenAI requires:
    - assistant message with tool_calls
    - followed by tool messages answering every tool_call_id
    """
    window = list(messages[-limit:])
    sanitized = []
    i = 0
    while i < len(window):
        msg = window[i]
        role = _message_role(msg)

        # Keep assistant tool-call messages only when all tool responses are present.
        if role == "ai" and _has_tool_calls(msg):
            required_ids = {call.get("id") for call in _tool_calls(msg) if isinstance(call, dict) and call.get("id")}
            tool_block = []
            answered_ids = set()
            j = i + 1
            while j < len(window) and _message_role(window[j]) == "tool":
                tool_msg = window[j]
                tool_block.append(tool_msg)
                call_id = _tool_call_id(tool_msg)
                if call_id:
                    answered_ids.add(call_id)
                j += 1

            if required_ids and required_ids.issubset(answered_ids):
                sanitized.append(msg)
                sanitized.extend(tool_block)
            # Else: drop the incomplete chain to avoid OpenAI 400 errors.
            i = j
            continue

        # Keep non-tool messages normally.
        if role != "tool":
            sanitized.append(msg)
        # Drop orphan tool messages.
        i += 1

    return sanitized
