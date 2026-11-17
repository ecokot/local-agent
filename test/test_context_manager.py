
import pytest

from local_agent.context_manager import ContextManager


def test_add_messages():
    context = ContextManager()

    context.add_message("system", "Ты помощник.")
    context.add_message("user", "Привет!")
    context.add_message("assistant", "Здравствуйте!")

    messages = context.get_messages()

    assert len(messages) == 3
    assert messages[0]["role"] == "system"
    assert messages[1]["content"] == "Привет!"
    assert messages[2]["role"] == "assistant"


def test_system_message_must_be_first():
    context = ContextManager()
    context.add_message("user", "Привет!")

    with pytest.raises(ValueError):
        context.add_message("system", "Инструкция")


def test_empty_message_is_rejected():
    context = ContextManager()

    with pytest.raises(ValueError):
        context.add_message("user", " ")


def test_clear_messages():
    context = ContextManager()
    context.add_message("user", "Привет!")

    context.clear()

    assert context.get_messages() == []


def test_get_messages_returns_copy():
    context = ContextManager()
    context.add_message("user", "Привет!")

    messages = context.get_messages()
    messages.clear()

    assert len(context.get_messages()) == 1
