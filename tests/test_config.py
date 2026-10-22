import pytest

from doc_sorter import ConfigError
from doc_sorter.config import PLACEHOLDER_API_KEY, resolve


def test_base_url_and_model_are_required():
    with pytest.raises(ConfigError) as exc:
        resolve()
    assert "base_url" in str(exc.value) and "model" in str(exc.value)


def test_api_key_is_optional():
    s = resolve(base_url="http://x/v1", model="m")
    assert s.api_key == PLACEHOLDER_API_KEY


def test_environment_variables(monkeypatch):
    monkeypatch.setenv("DOC_SORTER_BASE_URL", "http://env/v1")
    monkeypatch.setenv("DOC_SORTER_MODEL", "text-model")
    monkeypatch.setenv("DOC_SORTER_API_KEY", "secret")
    monkeypatch.setenv("DOC_SORTER_MAX_FILES", "7")
    s = resolve()
    assert (s.base_url, s.model, s.api_key, s.max_files) == ("http://env/v1", "text-model", "secret", 7)


def test_argument_wins_over_environment(monkeypatch):
    monkeypatch.setenv("DOC_SORTER_MODEL", "env-model")
    assert resolve(base_url="http://x/v1", model="arg-model").model == "arg-model"


def test_openai_api_key_is_used_as_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert resolve(base_url="http://x/v1", model="m").api_key == "sk-test"


def test_vision_model_defaults_to_model():
    assert resolve(base_url="http://x/v1", model="m").vision_model == "m"
    assert resolve(base_url="http://x/v1", model="m", vision_model="v").vision_model == "v"


def test_invalid_values(monkeypatch):
    with pytest.raises(ConfigError):
        resolve(base_url="http://x/v1", model="m", max_files=0)
    monkeypatch.setenv("DOC_SORTER_TIMEOUT", "soon")
    with pytest.raises(ConfigError):
        resolve(base_url="http://x/v1", model="m")
