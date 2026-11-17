
from collections.abc import Mapping


class ModelRouter:
    def __init__(self, models: Mapping[str, str]) -> None:
        if not models:
            raise ValueError("Models configuration cannot be empty")

        for role, model in models.items():
            if not role.strip():
                raise ValueError("Model role cannot be empty")

            if not model.strip():
                raise ValueError(f"Model ID for role '{role}' cannot be empty")

        self.models = dict(models)

    def get_model(self, role: str) -> str:
        try:
            return self.models[role]
        except KeyError:
            raise ValueError(f"Unknown model role: {role}") from None
