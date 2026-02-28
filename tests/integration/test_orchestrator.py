"""
Integration tests for agent.orchestrator — multi-round loop, termination, overfitting rollback.

All external dependencies (DeepSeek API, freqtrade) are mocked.
"""

import json
import os
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from agent.orchestrator import Orchestrator
from agent.evaluator import EvalResult


# ---------------------------------------------------------------------------
# Shared helpers
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


def _base_config(tmp_path) -> dict:
    """Return a minimal agent config dict pointing at tmp_path."""
    strategy_dir = tmp_path / "strategies"
    strategy_dir.mkdir()
    (strategy_dir / "LotteryMindsetStrategy.py").write_text(VALID_STRATEGY_CODE)

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    results_dir = tmp_path / "results"
    results_dir.mkdir()

    return {
        "max_rounds": 5,
        "stale_rounds_limit": 3,
        "deepseek_model": "deepseek-chat",
        "freqtrade_dir": str(tmp_path / "ft"),
        "strategy_name": "LotteryMindsetStrategy",
        "config_path": "config/config_backtest.json",
        "timerange_is": "20250901-20251130",
        "timerange_oos": "20251201-20251231",
        "enable_walk_forward": False,
        "strategy_dir": str(strategy_dir),
        "backup_dir": str(backup_dir),
        "results_dir": str(results_dir),
    }


def _good_metrics(score_tweak: float = 0.0) -> dict:
    """Metrics dict that passes all gate checks."""
    return {
        "weekly_target_hit_rate": 0.30,
        "max_drawdown_pct": 50.0,
        "total_trades": 100,
        "stake_limit_hit_count": 0,
        "monthly_net_profit_avg": 50.0 + score_tweak,
        "max_monthly_loss": 100.0,
        "avg_trade_duration_hours": 24.0,
        "avg_profit_per_trade_pct": 1.5,
    }


def _llm_response(round_num: int = 1) -> dict:
    """Fake DeepSeek patch response."""
    return {
        "round": round_num,
        "changes_made": f"Adjusted ADX threshold (round {round_num})",
        "rationale": "Backtest showed low trade count",
        "code_patch": VALID_STRATEGY_CODE,
        "config_patch": {},
        "next_action": "continue",
    }


def _make_orchestrator(config: dict) -> Orchestrator:
    """
    Build an Orchestrator with all external I/O replaced by mocks.

    The DeepSeek client and BacktestRunner are wired to sensible defaults.
    Callers can further customise via ``orch.deepseek_client`` /
    ``orch.backtest_runner`` attributes.
    """
    # Patch DeepSeekClient constructor so it doesn't require an API key
    with patch("agent.orchestrator.DeepSeekClient"):
        orch = Orchestrator(config)

    # Replace sub-components with mocks ---
    # DeepSeek
    mock_ds = MagicMock()
    mock_ds.generate_strategy_patch.return_value = _llm_response()
    orch.deepseek_client = mock_ds

    # BacktestRunner
    mock_bt = MagicMock()
    mock_bt.run.return_value = {
        "success": True,
        "error": "",
        "metrics": _good_metrics(),
        "raw_results": {},
        "result_file": "/tmp/fake.json",
    }
    orch.backtest_runner = mock_bt

    # System prompt (no file I/O)
    orch.system_prompt = "You are a test agent."

    return orch


# ===================================================================
# T021-1: test_single_round_iteration
# ===================================================================


class TestSingleRoundIteration:
    """单轮执行完整流程（mock DeepSeek + backtest）"""

    def test_single_round_iteration(self, tmp_path):
        """One round: LLM → patch → backtest → evaluate → record."""
        config = _base_config(tmp_path)
        config["max_rounds"] = 1
        orch = _make_orchestrator(config)

        rounds = orch.run_iteration_loop(max_rounds=1)

        assert len(rounds) == 1
        r = rounds[0]
        assert r["round"] == 1
        assert r["status"] == "success"
        assert r["score"] > 0
        assert r["changes_made"] != ""
        assert r["rationale"] != ""
        assert r["backtest_metrics"] != {}
        assert r["eval_result"]["passed"] is True

        # Sub-components called exactly once
        orch.deepseek_client.generate_strategy_patch.assert_called_once()
        orch.backtest_runner.run.assert_called_once()


# ===================================================================
# T021-2: test_multi_round_terminates_on_max_rounds
# ===================================================================


class TestMultiRoundMaxRounds:
    """设置 max_rounds=3，执行 3 轮后停止。"""

    def test_multi_round_terminates_on_max_rounds(self, tmp_path):
        config = _base_config(tmp_path)
        orch = _make_orchestrator(config)

        # Give each round a progressively higher score so stale-check
        # does NOT trigger before max_rounds=3.
        call_count = {"n": 0}
        original_metrics = _good_metrics()

        def _increasing_bt(*args, **kwargs):
            call_count["n"] += 1
            m = dict(original_metrics)
            m["monthly_net_profit_avg"] = 50.0 + call_count["n"] * 10
            return {
                "success": True,
                "error": "",
                "metrics": m,
                "raw_results": {},
                "result_file": "",
            }

        orch.backtest_runner.run.side_effect = _increasing_bt

        def _llm_side(*args, **kwargs):
            return _llm_response(round_num=call_count["n"] + 1)

        orch.deepseek_client.generate_strategy_patch.side_effect = _llm_side

        rounds = orch.run_iteration_loop(max_rounds=3)

        assert len(rounds) == 3
        for i, r in enumerate(rounds, 1):
            assert r["round"] == i
            assert r["status"] == "success"


# ===================================================================
# T021-3: test_terminates_on_stale_rounds
# ===================================================================


class TestStaleRoundsTermination:
    """连续 3 轮 score 无提升时提前终止。"""

    def test_terminates_on_stale_rounds(self, tmp_path):
        config = _base_config(tmp_path)
        config["stale_rounds_limit"] = 3
        orch = _make_orchestrator(config)

        # All rounds return the SAME score → stale after 3 successful rounds
        fixed_metrics = _good_metrics()  # score is constant
        orch.backtest_runner.run.return_value = {
            "success": True,
            "error": "",
            "metrics": fixed_metrics,
            "raw_results": {},
            "result_file": "",
        }

        rounds = orch.run_iteration_loop(max_rounds=10)

        # Should stop at exactly 3 (stale_rounds_limit)
        assert len(rounds) == 3
        assert "STOP" in rounds[-1]["next_action"]


# ===================================================================
# T021-4: test_overfitting_rollback
# ===================================================================


class TestOverfittingRollback:
    """OOS/IS < 0.6 时回退。"""

    def test_overfitting_rollback(self, tmp_path):
        config = _base_config(tmp_path)
        config["enable_walk_forward"] = True
        config["max_rounds"] = 2
        orch = _make_orchestrator(config)

        # Round 1: success, increasing scores so no stale
        call_count = {"n": 0}

        def _bt_side(*args, **kwargs):
            call_count["n"] += 1
            m = dict(_good_metrics())
            m["monthly_net_profit_avg"] = 50.0 + call_count["n"] * 10
            return {
                "success": True,
                "error": "",
                "metrics": m,
                "raw_results": {},
                "result_file": "",
            }

        orch.backtest_runner.run.side_effect = _bt_side

        # Walk-forward fails (OOS/IS < 0.6)
        orch.run_walk_forward = MagicMock(
            return_value=(False, "OVERFITTING: OOS/IS ratio = 0.40 < 0.6 threshold.")
        )

        # Spy on rollback
        orch.strategy_modifier.rollback = MagicMock(return_value=True)

        rounds = orch.run_iteration_loop(max_rounds=2)

        # At least the first round should be flagged overfitting
        overfitting_rounds = [r for r in rounds if r["status"] == "overfitting"]
        assert len(overfitting_rounds) >= 1
        assert "OVERFITTING" in overfitting_rounds[0]["next_action"]

        # rollback should have been called for the overfitting round
        assert orch.strategy_modifier.rollback.called


# ===================================================================
# T021-5: test_iteration_log_written
# ===================================================================


class TestIterationLogWritten:
    """每轮结果写入 iteration_log.json。"""

    def test_iteration_log_written(self, tmp_path):
        config = _base_config(tmp_path)
        config["max_rounds"] = 2
        orch = _make_orchestrator(config)

        # Increasing scores to avoid stale stop
        call_count = {"n": 0}

        def _bt_side(*args, **kwargs):
            call_count["n"] += 1
            m = dict(_good_metrics())
            m["monthly_net_profit_avg"] = 50.0 + call_count["n"] * 10
            return {
                "success": True,
                "error": "",
                "metrics": m,
                "raw_results": {},
                "result_file": "",
            }

        orch.backtest_runner.run.side_effect = _bt_side

        rounds = orch.run_iteration_loop(max_rounds=2)

        log_path = os.path.join(str(tmp_path / "results"), "iteration_log.json")
        assert os.path.exists(log_path)

        with open(log_path) as f:
            log_data = json.load(f)

        assert len(log_data) == len(rounds)
        for entry in log_data:
            assert "round" in entry
            assert "timestamp" in entry
            assert "status" in entry
            assert "score" in entry


# ===================================================================
# Edge-case: backtest failure doesn't crash the loop
# ===================================================================


class TestBacktestFailureRecovery:
    """回测失败记录 failed，循环继续。"""

    def test_backtest_failure_continues(self, tmp_path):
        config = _base_config(tmp_path)
        config["max_rounds"] = 3
        orch = _make_orchestrator(config)

        call_count = {"n": 0}

        def _bt_side(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return {
                    "success": False,
                    "error": "freqtrade crashed",
                    "metrics": {},
                    "raw_results": {},
                    "result_file": "",
                }
            m = dict(_good_metrics())
            m["monthly_net_profit_avg"] = 50.0 + call_count["n"] * 20
            return {
                "success": True,
                "error": "",
                "metrics": m,
                "raw_results": {},
                "result_file": "",
            }

        orch.backtest_runner.run.side_effect = _bt_side

        rounds = orch.run_iteration_loop(max_rounds=3)

        assert len(rounds) == 3
        assert rounds[0]["status"] == "failed"
        assert rounds[1]["status"] == "success"
        assert rounds[2]["status"] == "success"
