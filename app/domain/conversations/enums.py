from enum import StrEnum


class ConversationChannel(StrEnum):
    CHAT = "chat"
    VOICE = "voice"
    RETELL_VOICE = "retell_voice"
    SYSTEM = "system"


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"
    ESCALATED = "escalated"
    ABANDONED = "abandoned"


class ConversationMessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"