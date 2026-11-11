"""LT-07: Gespraechskontext aus der bestehenden NEXUS-Conversation fuer Live Talk."""
from __future__ import annotations


def build_messages(conversation: list[dict], system_prompt: str, max_turns: int = 12) -> list[dict]:
    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    # Sichern wir uns die letzten max_turns Einträge
    recent_items = conversation[-max_turns:]

    for item in recent_items:
        role = item.get("role")
        content_val = item.get("text") or item.get("content")

        if role in ("user", "assistant"):
            if content_val is not None and content_val != "":
                messages.append({"role": role, "content": content_val})

    return messages


def append_turn(conversation: list[dict], user: str, reply: str) -> list[dict]:
    # Wir arbeiten IN-PLACE mit der übergebenen Liste, um die Referenz (`is c`) zu wahren.
    if user:
        conversation.append({"role": "user", "content": user})

        if reply:
            conversation.append({"role": "assistant", "content": reply, "source": "livetalk"})

    return conversation
