
import pytest

from local_agent.model_router import ModelRouter


def test_get_model_returns_correct_model():
    router = ModelRouter({
        "planner": "qwen3.5_4b",
        "coder": "qwen2.5-coder-7b-instruct",
    })

    assert router.get_model("coder") == "qwen2.5-coder-7b-instruct"


def test_get_model_rejects_unknown_role():
    router = ModelRouter({
        "coder": "qwen2.5-coder-7b-instruct",
    })

    with pytest.raises(ValueError, match="Unknown model role"):
        router.get_model("unknown")


def test_empty_models_are_rejected():
    with pytest.raises(ValueError):
        ModelRouter({})
