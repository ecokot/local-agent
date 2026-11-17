
from typing import Literal

MessageRole = Literal["system", "user", "assistant"]


class ContextManager:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    def add_message(self, role: MessageRole, content: str) -> None:
        if role not in ("system", "user", "assistant"):
            raise ValueError(f"Unsupported message role: {role}")

        if not content.strip():
            raise ValueError("Message content cannot be empty")

        if role == "system" and self.messages:
            raise ValueError("System message must be the first message")

        self.messages.append({
            "role": role,
            "content": content,
        })

    def get_messages(self) -> list[dict[str, str]]:
        return [message.copy() for message in self.messages]

    def clear(self) -> None:
        self.messages.clear()
