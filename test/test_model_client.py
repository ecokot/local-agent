
from unittest.mock import MagicMock, patch

import pytest

from local_agent.model_client import (
    ModelClient,
    ModelResponseError,
)


def test_complete_returns_model_response():
    fake_response = MagicMock()
    fake_response.choices[0].message.content = "Hello!"

    with patch("local_agent.model_client.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = (
            fake_response
        )

        client = ModelClient()
        result = client.complete(
            model="test-model",
            messages=[{"role": "user", "content": "Hello"}],
        )

    assert result == "Hello!"


def test_complete_rejects_empty_response():
    fake_response = MagicMock()
    fake_response.choices = []

    with patch("local_agent.model_client.OpenAI") as mock_openai:
        mock_openai.return_value.chat.completions.create.return_value = (
            fake_response
        )

        client = ModelClient()

        with pytest.raises(ModelResponseError):
            client.complete(
                model="test-model",
                messages=[{"role": "user", "content": "Hello"}],
            )
