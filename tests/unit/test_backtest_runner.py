"""
Unit tests for agent.backtest_runner — command build, metrics extraction, run lifecycle.
"""

import json
import os
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from agent.backtest_runner import BacktestRunner


# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------

STRATEGY_NAME = "LotteryMindsetStrategy"


def _make_raw_result(**overrides) -> dict:
    """Return a realistic freqtrade backtest JSON; override strategy-level fields."""
    strat_data = {
        "trades": [
            {
                "close_date": "2025-10-01T12:00:00Z",
                "profit_abs": 1200.0,
                "trade_duration": 210,  # minutes
                "stake_amount_error": False,
            },
            {
                "close_date": "2025-10-08T14:00:00Z",
                "profit_abs": 800.0,
                "trade_duration": 180,
                "stake_amount_error": False,
            },
            {
                "close_date": "2025-10-15T10:00:00Z",
                "profit_abs": -50.0,
                "trade_duration": 90,
                "stake_amount_error": False,
            },
        ],
        "results_per_pair": [],
        "total_trades": 120,
        "profit_total": 1.5,
        "profit_total_abs": 150.0,
        "max_drawdown": 0.45,
        "max_drawdown_abs": 45.0,
        "sharpe": 1.2,
        "sortino": 1.8,
        "profit_factor": 2.1,
        "win_rate": 0.35,
        "profit_mean": 0.0125,
        "holding_avg": "3:30:00",
        "trade_count": 120,
        "best_pair": "BTC/USDT",
        "worst_pair": "DOGE/USDT",
        "backtest_start": "2025-09-01",
        "backtest_end": "2025-12-31",
    }
    strat_data.update(overrides)
    return {
        "strategy": {STRATEGY_NAME: strat_data},
        "strategy_comparison": [],
    }


@pytest.fixture
def runner(tmp_path) -> BacktestRunner:
    """BacktestRunner with temp paths so no real I/O is hit."""
    return BacktestRunner(
        freqtrade_dir=str(tmp_path / "ft"),
        user_data="user_data",
        results_dir=str(tmp_path / "results"),
        config_path="config/config_backtest.json",
        strategy_name=STRATEGY_NAME,
    )


# ===================================================================
# _build_command
# ===================================================================


class TestBuildCommand:
    """命令构建测试组"""

    def test_build_command_basic(self, runner: BacktestRunner):
        """基础命令包含 freqtrade backtesting, --config, --strategy, --export trades"""
        cmd = runner._build_command("config/config_backtest.json", None)

        assert cmd[0] == "freqtrade"
        assert cmd[1] == "backtesting"
        assert "--config" in cmd
        assert cmd[cmd.index("--config") + 1] == "config/config_backtest.json"
        assert "--strategy" in cmd
        assert cmd[cmd.index("--strategy") + 1] == STRATEGY_NAME
        assert "--export" in cmd
        assert cmd[cmd.index("--export") + 1] == "trades"
        # 不应包含 --timerange（未传入）
        assert "--timerange" not in cmd

    def test_build_command_with_timerange(self, runner: BacktestRunner):
        """传入 timerange 后命令包含 --timerange"""
        cmd = runner._build_command("cfg.json", "20250901-20251231")

        assert "--timerange" in cmd
        assert cmd[cmd.index("--timerange") + 1] == "20250901-20251231"

    def test_build_command_with_extra_args(self, tmp_path):
        """额外参数被追加到命令尾部"""
        r = BacktestRunner(
            freqtrade_dir=str(tmp_path),
            extra_args=["--fee", "0.001", "--eps"],
        )
        cmd = r._build_command("c.json", None)

        assert "--fee" in cmd
        assert "0.001" in cmd
        assert "--eps" in cmd
        # extra_args 应在末尾
        fee_idx = cmd.index("--fee")
        assert fee_idx > cmd.index("--export")


# ===================================================================
# _extract_metrics
# ===================================================================


class TestExtractMetrics:
    """结果解析测试组"""

    def test_extract_metrics_basic(self):
        """给定 mock freqtrade raw JSON，提取出标准化 metrics"""
        raw = _make_raw_result()
        metrics = BacktestRunner._extract_metrics(raw)

        # Core scalar metrics
        assert metrics["total_profit_pct"] == pytest.approx(150.0)  # 1.5 * 100
        assert metrics["total_profit_abs"] == 150.0
        assert metrics["max_drawdown_pct"] == pytest.approx(45.0)  # 0.45 * 100
        assert metrics["sharpe_ratio"] == 1.2
        assert metrics["sortino_ratio"] == 1.8
        assert metrics["profit_factor"] == 2.1
        assert metrics["win_rate"] == 0.35
        assert metrics["total_trades"] == 120
        assert metrics["avg_profit_per_trade_pct"] == pytest.approx(1.25)  # 0.0125 * 100
        assert metrics["avg_trade_duration"] == "3:30:00"
        assert metrics["best_pair"] == "BTC/USDT"
        assert metrics["worst_pair"] == "DOGE/USDT"

        # Trade-derived metrics
        assert metrics["total_trades_count"] == 3  # len(trades)
        assert metrics["stake_limit_hit_count"] == 0
        assert "weekly_target_hit_rate" in metrics
        assert "avg_trade_duration_hours" in metrics

    def test_extract_metrics_empty(self):
        """raw 结果为空时返回 error 提示"""
        metrics = BacktestRunner._extract_metrics({})
        assert metrics.get("error") == "No strategy results found"

    def test_extract_metrics_empty_strategy(self):
        """strategy 字典存在但无策略 → error"""
        metrics = BacktestRunner._extract_metrics({"strategy": {}})
        assert metrics.get("error") == "No strategy results found"

    def test_extract_metrics_no_trades(self):
        """策略有结果但 trades 为空 → 所有 trade-derived 指标为 0"""
        raw = _make_raw_result(trades=[])
        metrics = BacktestRunner._extract_metrics(raw)

        assert metrics["stake_limit_hit_count"] == 0
        assert metrics["weekly_target_hit_rate"] == 0
        assert metrics["avg_trade_duration_hours"] == 0

    def test_extract_metrics_stake_limit_hits(self):
        """交易中有 stake_amount_error → 被计入 stake_limit_hit_count"""
        trades = [
            {"close_date": "2025-10-01T12:00:00Z", "profit_abs": 100, "trade_duration": 60, "stake_amount_error": True},
            {"close_date": "2025-10-02T12:00:00Z", "profit_abs": 200, "trade_duration": 60, "stake_amount_error": True},
            {"close_date": "2025-10-03T12:00:00Z", "profit_abs": 50, "trade_duration": 60, "stake_amount_error": False},
        ]
        raw = _make_raw_result(trades=trades)
        metrics = BacktestRunner._extract_metrics(raw)
        assert metrics["stake_limit_hit_count"] == 2


# ===================================================================
# _calc_weekly_metrics
# ===================================================================


class TestCalcWeeklyMetrics:
    """周达标率计算测试组"""

    def test_calc_weekly_target_hit_rate(self):
        """给定交易数据，计算周达标率"""
        # Week 1: 1200 >= 1000 → hit
        # Week 2: 800 < 1000 → miss
        # Week 3: -50 < 1000 → miss
        trades = [
            {"close_date": "2025-10-01T12:00:00Z", "profit_abs": 1200.0},
            {"close_date": "2025-10-08T14:00:00Z", "profit_abs": 800.0},
            {"close_date": "2025-10-15T10:00:00Z", "profit_abs": -50.0},
        ]
        result = BacktestRunner._calc_weekly_metrics(trades)

        assert result["total_weeks"] == 3
        assert result["target_hit_weeks"] == 1
        assert result["weekly_target_hit_rate"] == pytest.approx(1 / 3, abs=0.01)

    def test_calc_weekly_metrics_all_hit(self):
        """所有周均达标"""
        trades = [
            {"close_date": "2025-10-01T12:00:00Z", "profit_abs": 2000.0},
            {"close_date": "2025-10-08T14:00:00Z", "profit_abs": 1500.0},
        ]
        result = BacktestRunner._calc_weekly_metrics(trades)
        assert result["weekly_target_hit_rate"] == 1.0
        assert result["target_hit_weeks"] == result["total_weeks"]

    def test_calc_weekly_metrics_empty(self):
        """空交易列表 → 0 值"""
        result = BacktestRunner._calc_weekly_metrics([])
        assert result["weekly_target_hit_rate"] == 0
        assert result["total_weeks"] == 0
        assert result["target_hit_weeks"] == 0
        assert result["monthly_net_profit_avg"] == 0
        assert result["max_monthly_loss"] == 0

    def test_calc_weekly_metrics_no_close_date(self):
        """交易无 close_date → 被跳过"""
        trades = [
            {"profit_abs": 1200.0},
            {"close_date": "", "profit_abs": 1200.0},
        ]
        result = BacktestRunner._calc_weekly_metrics(trades)
        assert result["total_weeks"] == 0

    def test_calc_weekly_metrics_same_week_aggregation(self):
        """同一周多笔交易利润被聚合"""
        # Both on 2025-10-01 (Wed) and 2025-10-03 (Fri) → same ISO week 40
        trades = [
            {"close_date": "2025-10-01T12:00:00Z", "profit_abs": 600.0},
            {"close_date": "2025-10-03T12:00:00Z", "profit_abs": 500.0},
        ]
        result = BacktestRunner._calc_weekly_metrics(trades)
        assert result["total_weeks"] == 1
        assert result["target_hit_weeks"] == 1  # 600 + 500 = 1100 >= 1000

    def test_calc_weekly_metrics_monthly_stats(self):
        """月度统计被正确计算"""
        trades = [
            {"close_date": "2025-10-01T12:00:00Z", "profit_abs": 2000.0},
            {"close_date": "2025-10-08T14:00:00Z", "profit_abs": -500.0},
        ]
        result = BacktestRunner._calc_weekly_metrics(trades)
        assert "monthly_net_profit_avg" in result
        assert "max_monthly_loss" in result
        assert isinstance(result["monthly_net_profit_avg"], float)
        assert isinstance(result["max_monthly_loss"], float)


# ===================================================================
# run()
# ===================================================================


class TestRun:
    """run() 执行生命周期测试组"""

    def test_run_success(self, runner: BacktestRunner, tmp_path):
        """mock subprocess.run 返回 success + 有结果文件 → success=True"""
        # Prepare a result file on disk
        result_dir = tmp_path / "results"
        result_dir.mkdir(parents=True, exist_ok=True)
        result_file = result_dir / "backtest-result.json"
        raw = _make_raw_result()
        result_file.write_text(json.dumps(raw))

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "ok"
        mock_proc.stderr = ""

        with patch("agent.backtest_runner.subprocess.run", return_value=mock_proc), \
             patch.object(runner, "_find_latest_result", return_value=str(result_file)):
            out = runner.run()

        assert out["success"] is True
        assert out["error"] == ""
        assert out["raw_results"] == raw
        assert "total_profit_pct" in out["metrics"]
        assert out["result_file"] == str(result_file)

    def test_run_backtest_failure(self, runner: BacktestRunner):
        """mock subprocess.run 返回 returncode=1 → success=False"""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = "some output"
        mock_proc.stderr = "Error: strategy not found"

        with patch("agent.backtest_runner.subprocess.run", return_value=mock_proc):
            out = runner.run()

        assert out["success"] is False
        assert "Error" in out["error"] or "strategy" in out["error"]
        assert out["raw_results"] == {}
        assert out["metrics"] == {}
        assert out["result_file"] == ""

    def test_run_timeout(self, runner: BacktestRunner):
        """mock subprocess.run 抛 TimeoutExpired → success=False, error 含 timeout"""
        with patch(
            "agent.backtest_runner.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="freqtrade", timeout=600),
        ):
            out = runner.run()

        assert out["success"] is False
        assert "timeout" in out["error"].lower() or "timed out" in out["error"].lower()
        assert out["raw_results"] == {}
        assert out["metrics"] == {}

    def test_run_no_result_file(self, runner: BacktestRunner):
        """命令成功但找不到结果文件 → success=False"""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "ok"
        mock_proc.stderr = ""

        with patch("agent.backtest_runner.subprocess.run", return_value=mock_proc), \
             patch.object(runner, "_find_latest_result", return_value=None):
            out = runner.run()

        assert out["success"] is False
        assert "No result file" in out["error"] or "result file" in out["error"].lower()
        assert out["raw_results"] == {}

    def test_run_generic_exception(self, runner: BacktestRunner):
        """subprocess.run 抛出意外异常 → success=False"""
        with patch(
            "agent.backtest_runner.subprocess.run",
            side_effect=OSError("freqtrade not found"),
        ):
            out = runner.run()

        assert out["success"] is False
        assert "freqtrade not found" in out["error"]

    def test_run_uses_overrides(self, runner: BacktestRunner, tmp_path):
        """run() 的 timerange / config_override 被正确传递到 _build_command"""
        result_dir = tmp_path / "results"
        result_dir.mkdir(parents=True, exist_ok=True)
        result_file = result_dir / "backtest-result.json"
        result_file.write_text(json.dumps(_make_raw_result()))

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = ""

        with patch("agent.backtest_runner.subprocess.run", return_value=mock_proc) as mock_run, \
             patch.object(runner, "_find_latest_result", return_value=str(result_file)):
            runner.run(timerange="20251001-20251231", config_override="custom.json")

        # Verify the command passed to subprocess.run
        called_cmd = mock_run.call_args[0][0]
        assert "--timerange" in called_cmd
        assert "20251001-20251231" in called_cmd
        assert "--config" in called_cmd
        assert "custom.json" in called_cmd


# ===================================================================
# _find_latest_result
# ===================================================================


class TestFindLatestResult:
    """结果文件发现逻辑"""

    def test_find_latest_result_by_glob(self, runner: BacktestRunner, tmp_path):
        """glob 找到多个文件 → 返回最新的"""
        results_dir = tmp_path / "ft" / runner.results_dir
        # results_dir 可能是绝对路径，直接用 runner.results_dir
        rd = tmp_path / "ft" / runner.results_dir
        rd.mkdir(parents=True, exist_ok=True)

        f1 = rd / "backtest-result-2025-10-01.json"
        f2 = rd / "backtest-result-2025-11-01.json"
        f1.write_text("{}")
        f2.write_text("{}")

        # Ensure f2 is newer via mtime
        os.utime(str(f1), (1000000, 1000000))
        os.utime(str(f2), (2000000, 2000000))

        result = runner._find_latest_result()
        assert result is not None
        assert "2025-11-01" in result

    def test_find_latest_result_none(self, tmp_path):
        """无匹配文件 → 返回 None"""
        r = BacktestRunner(
            freqtrade_dir=str(tmp_path / "empty_ft"),
            results_dir=str(tmp_path / "empty_results"),
        )
        (tmp_path / "empty_ft").mkdir(parents=True, exist_ok=True)
        result = r._find_latest_result()
        assert result is None

    def test_find_latest_result_via_meta(self, runner: BacktestRunner, tmp_path):
        """通过 .last_result.json meta 文件定位结果"""
        base = tmp_path / "ft"
        rd = base / runner.results_dir
        rd.mkdir(parents=True, exist_ok=True)

        # 创建实际结果文件
        actual_file = rd / "backtest-result-20251201.json"
        actual_file.write_text("{}")

        # 创建 meta 文件
        meta = rd / ".last_result.json"
        meta.write_text(json.dumps({"latest_backtest": "backtest-result-20251201.json"}))

        # 删除匹配 backtest-result*.json 的主 glob（模拟 glob 失败）
        # 实际上 _find_latest_result 先 glob backtest-result*.json，
        # 如果找得到就不走 meta 路径，所以这里直接测 meta 逻辑比较困难。
        # 改为 mock glob 使主 pattern 返回空
        with patch("agent.backtest_runner.glob.glob") as mock_glob:
            # 第一次调用 (backtest-result*.json) 返回空
            # 第二次调用 (.last_result.json) 返回 meta
            mock_glob.side_effect = [
                [],  # no backtest-result*.json
                [str(meta)],  # .last_result.json found
            ]
            with patch("agent.backtest_runner.os.path.exists", return_value=True):
                result = runner._find_latest_result()

        assert result is not None
        assert "backtest-result-20251201.json" in result
