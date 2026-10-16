"""Chat session behavior preserved from the original assistant."""

from __future__ import annotations

SYSTEM_PROMPT = (
    "You are a helpful personal AI assistant. "
    "Help with programming, learning, writing, "
    "and everyday tasks. Reply in Bangla when "
    "the user writes in Bangla."
)


def escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


class ChatSession:
    """Message history for the personal chat, including the original system prompt."""

    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_assistant(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})

    def fail_last_turn(self) -> None:
        """Drop a user turn that never received a reply, matching the original app."""
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages.pop()

    def reset(self) -> None:
        self.messages = [self.messages[0]]

    def transcript(self, limit: int = 12) -> str:
        lines = []
        visible = [item for item in self.messages if item["role"] != "system"]
        for item in visible[-limit:]:
            speaker = "User" if item["role"] == "user" else "Assistant"
            lines.append(f"{speaker}: {item['content']}")
        return "\n".join(lines)
