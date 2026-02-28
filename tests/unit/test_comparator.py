"""
Unit tests for agent.comparator — multi-window backtesting, robustness scoring,
dry-run deviation, and comparison matrix construction.
"""

from unittest.mock import MagicMock

import pytest

from agent.comparator import Comparator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bt_runner(window_metrics: dict[str, dict] | None = None):
    """Return a mock BacktestRunner whose .run() returns per-window metrics."""
    runner = MagicMock()
    if window_metrics is None:
        window_metrics = {}

    def _run_side(timerange=None, **kwargs):
        # Match timerange to the window_metrics mapping
        for _label, m in window_metrics.items():
            if m.get("_timerange") == timerange:
                return {
                    "success": True,
                    "error": "",
                    "metrics": {k: v for k, v in m.items() if k != "_timerange"},
                    "raw_results": {},
                    "result_file": "",
                }
        # Fallback — return generic success
        return {
            "success": True,
            "error": "",
            "metrics": {"score": 50.0, "monthly_net_profit_avg": 50.0},
            "raw_results": {},
            "result_file": "",
        }

    runner.run.side_effect = _run_side
    return runner


# ===================================================================
# T047-1: test_aggregate_multi_window
# ===================================================================


class TestAggregateMultiWindow:
    """聚合 3 个窗口 (bull/bear/sideways) 的回测结果。"""

    def test_aggregate_multi_window(self):
        windows = {
            "bull": "20250101-20250301",
            "bear": "20250401-20250601",
            "sideways": "20250701-20250901",
        }
        runner = _make_bt_runner({
            "bull": {"_timerange": "20250101-20250301", "score": 80.0, "monthly_net_profit_avg": 120.0},
            "bear": {"_timerange": "20250401-20250601", "score": 40.0, "monthly_net_profit_avg": 30.0},
            "sideways": {"_timerange": "20250701-20250901", "score": 60.0, "monthly_net_profit_avg": 70.0},
        })

        comp = Comparator(backtest_runner=runner)
        result = comp.run_multi_window(windows)

        assert "metrics_by_window" in result
        assert set(result["metrics_by_window"].keys()) == {"bull", "bear", "sideways"}
        assert result["metrics_by_window"]["bull"]["score"] == 80.0
        assert result["metrics_by_window"]["bear"]["score"] == 40.0
        assert result["metrics_by_window"]["sideways"]["score"] == 60.0
        assert "robustness_score" in result
        assert runner.run.call_count == 3


# ===================================================================
# T047-2: test_robustness_score
# ===================================================================


class TestRobustnessScore:
    """低方差 → 高稳健性评分。"""

    def test_robustness_score_low_variance(self):
        """All windows have near-identical scores → high robustness."""
        runner = _make_bt_runner({
            "w1": {"_timerange": "20250101-20250301", "score": 60.0},
            "w2": {"_timerange": "20250401-20250601", "score": 60.0},
            "w3": {"_timerange": "20250701-20250901", "score": 60.0},
        })
        comp = Comparator(backtest_runner=runner)
        result = comp.run_multi_window({
            "w1": "20250101-20250301",
            "w2": "20250401-20250601",
            "w3": "20250701-20250901",
        })
        # Identical scores → CV = 0 → robustness = 100
        assert result["robustness_score"] == 100.0

    def test_robustness_score_high_variance(self):
        """Wide spread of scores → lower robustness."""
        comp = Comparator(backtest_runner=MagicMock())
        metrics = {
            "w1": {"score": 10.0},
            "w2": {"score": 50.0},
            "w3": {"score": 90.0},
        }
        score = comp._calc_robustness(metrics)
        assert score < 100.0
        assert score >= 0.0


# ===================================================================
# T047-3: test_dryrun_deviation
# ===================================================================


class TestDryrunDeviation:
    """计算 Dry Run 与回测的偏差指标 (price/signal/PnL)。"""

    def test_dryrun_deviation(self):
        comp = Comparator(backtest_runner=MagicMock())

        bt = {"avg_entry_price": 100.0, "total_trades": 50, "monthly_net_profit_avg": 200.0}
        dr = {"avg_entry_price": 105.0, "total_trades": 45, "monthly_net_profit_avg": 180.0}

        dev = comp.compute_dryrun_deviation(bt, dr)

        assert "price_slippage_pct" in dev
        assert "signal_gap_pct" in dev
        assert "pnl_gap_pct" in dev
        assert dev["price_slippage_pct"] == pytest.approx(5.0, abs=0.01)
        assert dev["signal_gap_pct"] == pytest.approx(10.0, abs=0.01)
        assert dev["pnl_gap_pct"] == pytest.approx(10.0, abs=0.01)

    def test_dryrun_deviation_zero_bt(self):
        """Backtest value is 0 — should not raise."""
        comp = Comparator(backtest_runner=MagicMock())
        bt = {"avg_entry_price": 0.0, "total_trades": 0, "monthly_net_profit_avg": 0.0}
        dr = {"avg_entry_price": 5.0, "total_trades": 10, "monthly_net_profit_avg": 50.0}

        dev = comp.compute_dryrun_deviation(bt, dr)
        assert dev["price_slippage_pct"] == 100.0
        assert dev["signal_gap_pct"] == 100.0
        assert dev["pnl_gap_pct"] == 100.0


# ===================================================================
# T047-4: test_comparison_matrix_output
# ===================================================================


class TestComparisonMatrixOutput:
    """输出格式包含 round/candidate_id/windows/robustness_score。"""

    def test_comparison_matrix_output(self):
        comp = Comparator(backtest_runner=MagicMock())

        mw_result = {
            "metrics_by_window": {
                "bull": {"score": 80.0},
                "bear": {"score": 40.0},
            },
            "robustness_score": 65.0,
        }
        dryrun_dev = {
            "price_slippage_pct": 3.5,
            "signal_gap_pct": 8.0,
            "pnl_gap_pct": 5.0,
        }

        matrix = comp.build_comparison_matrix(
            round_num=2,
            candidate_id="cand_001",
            multi_window_result=mw_result,
            dryrun_deviation=dryrun_dev,
        )

        assert matrix["round"] == 2
        assert matrix["candidate_id"] == "cand_001"
        assert set(matrix["windows"]) == {"bull", "bear"}
        assert matrix["robustness_score"] == 65.0
        assert matrix["dryrun_price_slippage_pct"] == 3.5
        assert matrix["dryrun_signal_gap_pct"] == 8.0
        assert matrix["dryrun_pnl_gap_pct"] == 5.0

    def test_comparison_matrix_no_dryrun(self):
        """Without dry-run deviation → deviation fields default to 0."""
        comp = Comparator(backtest_runner=MagicMock())
        mw = {"metrics_by_window": {"w1": {"score": 50.0}}, "robustness_score": 50.0}

        matrix = comp.build_comparison_matrix(1, "c1", mw)
        assert matrix["dryrun_price_slippage_pct"] == 0.0
        assert matrix["dryrun_signal_gap_pct"] == 0.0
        assert matrix["dryrun_pnl_gap_pct"] == 0.0


# ===================================================================
# T047-5: test_empty_windows
# ===================================================================


class TestEmptyWindows:
    """无窗口数据时安全返回。"""

    def test_empty_windows(self):
        runner = MagicMock()
        comp = Comparator(backtest_runner=runner)

        result = comp.run_multi_window({})

        assert result["metrics_by_window"] == {}
        assert result["robustness_score"] == 0.0
        runner.run.assert_not_called()

    def test_single_window(self):
        """Only one window → robustness = 0 (need ≥2 for CV)."""
        runner = MagicMock()
        runner.run.return_value = {
            "success": True,
            "error": "",
            "metrics": {"score": 70.0},
            "raw_results": {},
            "result_file": "",
        }
        comp = Comparator(backtest_runner=runner)
        result = comp.run_multi_window({"only": "20250101-20250301"})
        assert result["robustness_score"] == 0.0


# ===================================================================
# Save matrix (filesystem sanity)
# ===================================================================


class TestSaveMatrix:
    """save_matrix 写入 JSON 文件。"""

    def test_save_matrix(self, tmp_path):
        comp = Comparator(backtest_runner=MagicMock(), output_dir=str(tmp_path))
        matrix = {"round": 1, "candidate_id": "c1", "robustness_score": 80.0}
        comp.save_matrix(matrix)

        import json
        path = tmp_path / "comparison_matrix.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["robustness_score"] == 80.0
