"""
Unit tests for agent.error_recovery — error classification, fix prompts, repair loop, rollback.

All external dependencies are mocked.
"""

import ast
from unittest.mock import MagicMock, patch

import pytest

from agent.error_recovery import ErrorRecoveryManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_STRATEGY_CODE = '''\
class WeeklyBudgetController:
    pass

class LotteryMindsetStrategy:
    stoploss = -0.95
    leverage = 5

    def can_open_trade(self):
        return True

    def confirm_trade_entry(self, pair, order_type, amount, rate, time_in_force, current_time, entry_tag, side, **kwargs):
        return True

    def populate_indicators(self, dataframe, metadata):
        return dataframe
'''


def _make_manager(max_retries: int = 3) -> ErrorRecoveryManager:
    """Build an ErrorRecoveryManager with all deps mocked."""
    return ErrorRecoveryManager(
        deepseek_client=MagicMock(),
        strategy_modifier=MagicMock(),
        backtest_runner=MagicMock(),
        max_retries=max_retries,
    )


# ===================================================================
# T038-1: test_classify_error_syntax
# ===================================================================


class TestClassifyErrorSyntax:
    """含 SyntaxError 的 traceback → 分类为 'syntax'"""

    def test_classify_error_syntax(self):
        mgr = _make_manager()
        tb = (
            "Traceback (most recent call last):\n"
            "  File 'strategy.py', line 42\n"
            "    def foo(\n"
            "SyntaxError: unexpected EOF while parsing"
        )
        assert mgr.classify_error(tb) == "syntax"

    def test_classify_indentation_error(self):
        mgr = _make_manager()
        tb = "IndentationError: unexpected indent at line 10"
        assert mgr.classify_error(tb) == "syntax"


# ===================================================================
# T038-2: test_classify_error_runtime
# ===================================================================


class TestClassifyErrorRuntime:
    """含 KeyError/NameError → 'runtime'"""

    def test_classify_key_error(self):
        mgr = _make_manager()
        assert mgr.classify_error("KeyError: 'close'") == "runtime"

    def test_classify_name_error(self):
        mgr = _make_manager()
        assert mgr.classify_error("NameError: name 'df' is not defined") == "runtime"

    def test_classify_attribute_error(self):
        mgr = _make_manager()
        assert mgr.classify_error("AttributeError: 'NoneType' has no attribute 'x'") == "runtime"

    def test_classify_type_error(self):
        mgr = _make_manager()
        assert mgr.classify_error("TypeError: unsupported operand") == "runtime"

    def test_classify_index_error(self):
        mgr = _make_manager()
        assert mgr.classify_error("IndexError: list index out of range") == "runtime"


# ===================================================================
# T038-3: test_classify_error_config
# ===================================================================


class TestClassifyErrorConfig:
    """含 config/json → 'config'"""

    def test_classify_config(self):
        mgr = _make_manager()
        assert mgr.classify_error("Error loading config file: invalid json") == "config"

    def test_classify_json_decode(self):
        mgr = _make_manager()
        assert mgr.classify_error("json.decoder.JSONDecodeError: ...") == "config"

    def test_classify_settings(self):
        mgr = _make_manager()
        assert mgr.classify_error("Invalid settings value for pair_whitelist") == "config"


# ===================================================================
# T038-4: test_classify_error_data
# ===================================================================


class TestClassifyErrorData:
    """含 data/download → 'data'"""

    def test_classify_data(self):
        mgr = _make_manager()
        assert mgr.classify_error("No data found for requested timerange") == "data"

    def test_classify_download(self):
        mgr = _make_manager()
        assert mgr.classify_error("Failed to download pair data") == "data"

    def test_classify_pairs(self):
        mgr = _make_manager()
        assert mgr.classify_error("No valid pairs available for backtesting") == "data"

    def test_classify_timerange(self):
        mgr = _make_manager()
        assert mgr.classify_error("Invalid timerange specification") == "data"

    def test_classify_unknown(self):
        mgr = _make_manager()
        assert mgr.classify_error("Something completely unrecognised happened") == "unknown"


# ===================================================================
# T038-5: test_build_fix_prompt
# ===================================================================


class TestBuildFixPrompt:
    """返回包含 error_type/traceback/代码片段的 prompt string"""

    def test_build_fix_prompt(self):
        mgr = _make_manager()
        prompt = mgr.build_fix_prompt(
            error_type="syntax",
            traceback="SyntaxError: unexpected EOF",
            code_snippet="def foo(:",
            changes_summary="Round 1 changes",
        )
        assert "syntax" in prompt
        assert "SyntaxError: unexpected EOF" in prompt
        assert "def foo(:" in prompt
        assert "Round 1 changes" in prompt
        # Should ask for JSON return format
        assert "code_patch" in prompt


# ===================================================================
# T038-6: test_attempt_fix_success
# ===================================================================


class TestAttemptFixSuccess:
    """mock LLM 返回修复代码 → 语法校验通过 → 回测成功"""

    def test_attempt_fix_success(self):
        mgr = _make_manager(max_retries=3)

        # LLM returns valid fix
        mgr.deepseek_client.generate_fix_patch.return_value = {
            "code_patch": VALID_STRATEGY_CODE,
            "fix_summary": "Fixed syntax error in populate_indicators",
        }

        # Strategy modifier accepts patch
        mgr.strategy_modifier.apply_patch.return_value = {
            "success": True,
            "backup_path": "/tmp/backup.py",
            "errors": [],
            "warnings": [],
        }

        # Backtest succeeds after fix
        good_metrics = {"total_trades": 100, "monthly_net_profit_avg": 50.0}
        mgr.backtest_runner.run.return_value = {
            "success": True,
            "error": "",
            "metrics": good_metrics,
            "raw_results": {},
            "result_file": "",
        }

        result = mgr.attempt_fix(
            error_log="SyntaxError: unexpected EOF",
            current_code="def foo(:",
            round_num=1,
        )

        assert result["success"] is True
        assert result["attempts"] == 1
        assert result["error_type"] == "syntax"
        assert result["fix_summary"] == "Fixed syntax error in populate_indicators"
        assert result["metrics"] == good_metrics


# ===================================================================
# T038-7: test_attempt_fix_max_retries
# ===================================================================


class TestAttemptFixMaxRetries:
    """连续 3 次修复失败 → 返回 exhausted"""

    def test_attempt_fix_max_retries(self):
        mgr = _make_manager(max_retries=3)

        # LLM returns valid-syntax code every time, but backtest keeps failing
        mgr.deepseek_client.generate_fix_patch.return_value = {
            "code_patch": VALID_STRATEGY_CODE,
            "fix_summary": "attempted fix",
        }
        mgr.strategy_modifier.apply_patch.return_value = {
            "success": True,
            "backup_path": "/tmp/backup.py",
            "errors": [],
            "warnings": [],
        }
        mgr.backtest_runner.run.return_value = {
            "success": False,
            "error": "freqtrade still crashes",
            "metrics": {},
            "raw_results": {},
            "result_file": "",
        }

        result = mgr.attempt_fix(
            error_log="KeyError: 'close'",
            current_code=VALID_STRATEGY_CODE,
            round_num=2,
        )

        assert result["success"] is False
        assert result["attempts"] == 3
        assert result["fix_summary"] == "exhausted"
        assert result["error_type"] == "runtime"


# ===================================================================
# T038-8: test_rollback_on_exhausted
# ===================================================================


class TestRollbackOnExhausted:
    """修复耗尽后自动回滚并标记 quarantined"""

    def test_rollback_on_exhausted(self):
        mgr = _make_manager()
        mgr.strategy_modifier.rollback.return_value = True

        result = mgr.rollback_on_exhausted(round_num=3)

        assert result["rolled_back"] is True
        assert result["rollback_round"] == 2
        assert result["status"] == "quarantined"
        mgr.strategy_modifier.rollback.assert_called_once_with(2)

    def test_rollback_fails(self):
        mgr = _make_manager()
        mgr.strategy_modifier.rollback.return_value = False

        result = mgr.rollback_on_exhausted(round_num=3)

        assert result["rolled_back"] is False
        assert result["status"] == "rollback_failed"

    def test_rollback_round_one_no_prior(self):
        mgr = _make_manager()

        result = mgr.rollback_on_exhausted(round_num=1)

        assert result["rolled_back"] is False
        assert result["status"] == "rollback_failed"
