from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from auto_policy_m12_deepseek_client import DeepSeekConfig, call_deepseek_chat, chat_completions_url


def test_chat_completions_url():
    assert chat_completions_url("https://api.deepseek.com") == "https://api.deepseek.com/v1/chat/completions"
    assert chat_completions_url("https://api.deepseek.com/v1") == "https://api.deepseek.com/v1/chat/completions"


def test_deepseek_config_from_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")
    config = DeepSeekConfig.from_env()
    assert config.api_key == "test-key"
    assert config.model == "deepseek-chat"
    assert config.temperature == 0.2
    assert config.max_tokens == 1200
    assert config.timeout == 180


def test_deepseek_config_requires_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        DeepSeekConfig.from_env()


@patch("auto_policy_m12_deepseek_client.urllib.request.urlopen")
def test_call_deepseek_chat(mock_urlopen):
    payload = {
        "choices": [{"message": {"content": '{"current_value":"x","change_relation":"replace"}'}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "model": "deepseek-chat",
    }
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(payload).encode("utf-8")
    mock_response.__enter__.return_value = mock_response
    mock_urlopen.return_value = mock_response

    config = DeepSeekConfig(
        api_key="test",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        temperature=0.2,
        max_tokens=1200,
        timeout=180,
    )
    result = call_deepseek_chat("prompt", config=config)
    assert result.content.startswith("{")
    assert result.prompt_tokens == 10
    assert result.completion_tokens == 5
    assert result.total_tokens == 15
