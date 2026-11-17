
from collections.abc import Sequence

from openai import (
    APIConnectionError,
    APITimeoutError,
    DefaultHttpx2Client,
    OpenAI,
    OpenAIError,
)


class ModelClientError(Exception):
    """Base exception for model client errors."""


class ModelConnectionError(ModelClientError):
    """Cannot connect to the model server."""


class ModelRequestError(ModelClientError):
    """The model server rejected or failed the request."""


class ModelResponseError(ModelClientError):
    """The model returned an invalid response."""


class ModelClient:
    def __init__(
        self,
        base_url: str = "http://localhost:1235/v1",
        api_key: str = "lm-studio",
        timeout: float = 120.0,
    ) -> None:
        self.client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            max_retries=0,
            http_client=DefaultHttpx2Client(trust_env=False),
        )

    def complete(
        self,
        model: str,
        messages: Sequence[dict[str, str]],
        temperature: float = 0.2,
    ) -> str:
        if not model.strip():
            raise ValueError("Model name cannot be empty")

        if not messages:
            raise ValueError("Messages cannot be empty")

        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=list(messages),
                temperature=temperature,
            )

        except (APIConnectionError, APITimeoutError) as exc:
            raise ModelConnectionError(
                "Cannot connect to the model server"
            ) from exc

        except OpenAIError as exc:
            raise ModelRequestError(
                f"Model API error: {exc}"
            ) from exc

        if not response.choices:
            raise ModelResponseError("Model returned no choices")

        content = response.choices[0].message.content

        if not isinstance(content, str) or not content.strip():
            raise ModelResponseError("Model returned empty content")

        return content

    def close(self) -> None:
        self.client.close()
