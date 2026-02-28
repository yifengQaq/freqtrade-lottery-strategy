"""
Unit tests for agent.deepseek_client.DeepSeekClient.

All tests use mocks — no real API calls are made.
Tests are written against the **spec interface**, not internal implementation details.
"""

import json
import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

from agent.deepseek_client import DeepSeekClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

API_KEY = "test-api-key-fake"


def _make_chat_response(content: str, status_code: int = 200) -> httpx.Response:
    """Build a fake httpx.Response for chat completions."""
    body = {
        "choices": [{"message": {"content": content}}],
        "usage": {"total_tokens": 42},
    }
    resp = httpx.Response(
        status_code=status_code,
        json=body,
        request=httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions"),
    )
    return resp


def _make_error_response(status_code: int) -> httpx.Response:
    """Build a fake httpx error response."""
    resp = httpx.Response(
        status_code=status_code,
        json={"error": {"message": "error"}},
        request=httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions"),
    )
    return resp


@pytest.fixture
def client():
    """Create a DeepSeekClient with a fake API key."""
    return DeepSeekClient(api_key=API_KEY)


# =========================================================================
# 初始化
# =========================================================================


class TestInitialization:
    """Tests for __init__ parameter handling."""

    def test_missing_api_key_raises(self):
        """不传 api_key 且无环境变量 → ValueError."""
        with patch.dict(os.environ, {}, clear=True):
            # Also remove the key if it already exists
            os.environ.pop("DEEPSEEK_API_KEY", None)
            with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
                DeepSeekClient()

    def test_custom_parameters(self):
        """传入自定义 model/temperature/max_tokens，验证属性值."""
        c = DeepSeekClient(
            api_key=API_KEY,
            model="deepseek-reasoner",
            temperature=0.7,
            max_tokens=8192,
        )
        assert c.model == "deepseek-reasoner"
        assert c.temperature == 0.7
        assert c.max_tokens == 8192
        c.close()


# =========================================================================
# 基础功能
# =========================================================================


class TestChat:
    """Tests for the chat() public method."""

    def test_chat_returns_content(self, client, mocker):
        """mock httpx 响应，验证返回 assistant content."""
        expected = "Hello from DeepSeek"
        mock_post = mocker.patch.object(
            client._client, "post", return_value=_make_chat_response(expected)
        )
        result = client.chat("You are helpful.", "Say hi")
        assert result == expected
        mock_post.assert_called_once()

    def test_chat_with_json_mode(self, client, mocker):
        """验证请求 payload 中包含 response_format."""
        mock_post = mocker.patch.object(
            client._client, "post", return_value=_make_chat_response('{"ok":true}')
        )
        client.chat(
            "You are helpful.",
            "Return JSON",
            response_format={"type": "json_object"},
        )
        # Inspect the JSON payload sent to httpx
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload is not None
        assert payload["response_format"] == {"type": "json_object"}


class TestChatWithHistory:
    """Tests for chat_with_history()."""

    def test_chat_with_history(self, client, mocker):
        """验证 history 被正确拼接到 messages 中."""
        mock_post = mocker.patch.object(
            client._client, "post", return_value=_make_chat_response("reply")
        )
        history = [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
        ]
        client.chat_with_history("system prompt", history, "follow-up question")

        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        messages = payload["messages"]

        # system + 2 history + 1 new user = 4 messages
        assert len(messages) == 4
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "system prompt"
        assert messages[1] == history[0]
        assert messages[2] == history[1]
        assert messages[3]["role"] == "user"
        assert messages[3]["content"] == "follow-up question"


# =========================================================================
# 高级接口 — generate_strategy_patch
# =========================================================================


class TestGenerateStrategyPatch:
    """Tests for generate_strategy_patch()."""

    VALID_PATCH = {
        "changes_made": "Adjusted RSI thresholds",
        "rationale": "RSI too sensitive in ranging market",
        "code_patch": "class Strategy: ...",
        "config_patch": {},
        "next_action": "continue",
        "round": 1,
    }

    def test_generate_strategy_patch_returns_dict(self, client, mocker):
        """mock chat 返回有效 JSON，验证返回 dict 包含 required fields."""
        mocker.patch.object(
            client, "chat", return_value=json.dumps(self.VALID_PATCH)
        )
        result = client.generate_strategy_patch(
            system_prompt="sys",
            current_strategy_code="class S: pass",
            backtest_results={"total_profit_pct": 5.0},
            iteration_round=1,
            previous_changes=[],
        )
        assert isinstance(result, dict)
        for field in ("changes_made", "rationale", "code_patch"):
            assert field in result

    def test_generate_strategy_patch_invalid_json(self, client, mocker):
        """mock chat 返回非 JSON 文本，验证尝试提取或报错."""
        # Return plain text wrapped in markdown fences — _extract_json_from_text
        # can parse this.
        wrapped = '```json\n' + json.dumps(self.VALID_PATCH) + '\n```'
        mocker.patch.object(client, "chat", return_value=wrapped)

        result = client.generate_strategy_patch(
            system_prompt="sys",
            current_strategy_code="class S: pass",
            backtest_results={},
            iteration_round=1,
            previous_changes=[],
        )
        assert isinstance(result, dict)
        assert "code_patch" in result

    def test_generate_strategy_patch_truly_invalid_json(self, client, mocker):
        """chat 返回完全无法解析的文本 → ValueError."""
        mocker.patch.object(
            client, "chat", return_value="This is just plain text with no JSON at all."
        )
        with pytest.raises(ValueError, match="Cannot extract JSON"):
            client.generate_strategy_patch(
                system_prompt="sys",
                current_strategy_code="class S: pass",
                backtest_results={},
                iteration_round=1,
                previous_changes=[],
            )

    def test_generate_strategy_patch_missing_fields(self, client, mocker):
        """mock chat 返回缺少必需字段的 JSON → ValueError."""
        incomplete = json.dumps({"changes_made": "foo"})  # missing rationale, code_patch
        mocker.patch.object(client, "chat", return_value=incomplete)

        with pytest.raises(ValueError, match="missing required field"):
            client.generate_strategy_patch(
                system_prompt="sys",
                current_strategy_code="class S: pass",
                backtest_results={},
                iteration_round=1,
                previous_changes=[],
            )


# =========================================================================
# 重试逻辑
# =========================================================================


class TestRetryLogic:
    """Tests for retry behaviour on 429, 5xx, and timeout."""

    def test_retry_on_429(self, client, mocker):
        """第一次返回 429，第二次成功，验证总共调用 2 次."""
        mocker.patch("time.sleep")  # skip wait

        error_resp = _make_error_response(429)
        success_resp = _make_chat_response("ok")

        mock_post = mocker.patch.object(
            client._client,
            "post",
            side_effect=[
                httpx.HTTPStatusError(
                    "rate limited", request=error_resp.request, response=error_resp
                ),
                success_resp,
            ],
        )

        result = client.chat("sys", "msg")
        assert result == "ok"
        assert mock_post.call_count == 2

    def test_retry_on_500(self, client, mocker):
        """第一次返回 500，第二次成功."""
        mocker.patch("time.sleep")

        error_resp = _make_error_response(500)
        success_resp = _make_chat_response("recovered")

        mock_post = mocker.patch.object(
            client._client,
            "post",
            side_effect=[
                httpx.HTTPStatusError(
                    "server error", request=error_resp.request, response=error_resp
                ),
                success_resp,
            ],
        )

        result = client.chat("sys", "msg")
        assert result == "recovered"
        assert mock_post.call_count == 2

    def test_max_retries_exhausted(self, mocker):
        """连续 3 次 429，验证最终抛出 RuntimeError."""
        mocker.patch("time.sleep")

        c = DeepSeekClient(api_key=API_KEY, max_retries=3)
        error_resp = _make_error_response(429)
        exc = httpx.HTTPStatusError(
            "rate limited", request=error_resp.request, response=error_resp
        )

        mocker.patch.object(c._client, "post", side_effect=[exc, exc, exc])

        with pytest.raises(RuntimeError, match="failed after 3 attempts"):
            c.chat("sys", "msg")

        c.close()

    def test_timeout_retry(self, client, mocker):
        """第一次超时，第二次成功."""
        mocker.patch("time.sleep")

        success_resp = _make_chat_response("after timeout")

        mocker.patch.object(
            client._client,
            "post",
            side_effect=[
                httpx.TimeoutException("timed out"),
                success_resp,
            ],
        )

        result = client.chat("sys", "msg")
        assert result == "after timeout"
