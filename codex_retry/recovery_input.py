"""The fixed continuation instruction and its per-dispatch origin marker."""

import uuid

CONTINUATION_TEXT = (
    "Continue the unfinished task in this conversation from the last confirmed state. "
    "Check prior tool results before acting; do not repeat completed work or external "
    "actions whose outcomes are uncertain. If the task is complete or blocked, state that briefly."
)
RECOVERY_TRIGGER_PREFIX = "codex_auto_retry_"
USER_REQUEST_TRIGGERS = ("composer", "app_tool_send_message")


def recovery_trigger(identifier):
    return RECOVERY_TRIGGER_PREFIX + str(uuid.UUID(identifier))


def recovery_id_from_trigger(trigger):
    if not isinstance(trigger, str) or not trigger.startswith(RECOVERY_TRIGGER_PREFIX):
        return None
    value = trigger[len(RECOVERY_TRIGGER_PREFIX) :]
    try:
        return value if str(uuid.UUID(value)) == value else None
    except (ValueError, AttributeError):
        return None


def own_recovery_message(event, turn, identifier):
    return bool(
        identifier
        and event["kind"] == "user_message"
        and event.get("recovery_input") is True
        and turn is not None
        and event.get("turn_id") in (None, turn)
    )
