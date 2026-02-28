"""
Comparator — multi-window backtesting and Dry Run deviation analysis.

Responsibilities:
1. Run the same strategy across multiple market-regime windows (bull/bear/sideways)
2. Compute robustness score from cross-window variance
3. Measure deviation between backtest projections and dry-run actuals
4. Build a ComparisonMatrix for LLM-guided parameter tuning
"""

import json
import logging
import os
import statistics
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class Comparator:
    """Multi-window backtest comparison and dry-run deviation analysis."""

    def __init__(self, backtest_runner, output_dir: str = "results/comparisons"):
        self.runner = backtest_runner
        self.output_dir = output_dir

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_multi_window(self, windows: dict[str, str], **kwargs) -> dict:
        """
        Run the strategy across multiple time windows.

        Args:
            windows: Mapping of window label → timerange string, e.g.
                     ``{"bull": "20250101-20250301", "bear": "20250401-20250601"}``
            **kwargs: Forwarded to ``backtest_runner.run()``.

        Returns::

            {
                "metrics_by_window": {"bull": {...}, "bear": {...}, ...},
                "robustness_score": float,
            }
        """
        if not windows:
            return {"metrics_by_window": {}, "robustness_score": 0.0}

        metrics_by_window: dict[str, dict] = {}

        for label, timerange in windows.items():
            logger.info("Running backtest for window '%s' (%s)", label, timerange)
            bt_result = self.runner.run(timerange=timerange, **kwargs)
            if bt_result.get("success"):
                metrics_by_window[label] = bt_result.get("metrics", {})
            else:
                logger.warning(
                    "Window '%s' backtest failed: %s",
                    label,
                    bt_result.get("error", "unknown"),
                )
                metrics_by_window[label] = {}

        robustness = self._calc_robustness(metrics_by_window)

        return {
            "metrics_by_window": metrics_by_window,
            "robustness_score": robustness,
        }

    def compute_dryrun_deviation(
        self, backtest_metrics: dict, dryrun_metrics: dict
    ) -> dict:
        """
        Compute deviation between backtest projections and dry-run actuals.

        Returns::

            {
                "price_slippage_pct": float,
                "signal_gap_pct": float,
                "pnl_gap_pct": float,
            }
        """
        def _pct_diff(bt_val: float, dr_val: float) -> float:
            if bt_val == 0:
                return 0.0 if dr_val == 0 else 100.0
            return abs(bt_val - dr_val) / abs(bt_val) * 100.0

        bt_price = backtest_metrics.get("avg_entry_price", 0.0)
        dr_price = dryrun_metrics.get("avg_entry_price", 0.0)

        bt_signals = backtest_metrics.get("total_trades", 0)
        dr_signals = dryrun_metrics.get("total_trades", 0)

        bt_pnl = backtest_metrics.get("monthly_net_profit_avg", 0.0)
        dr_pnl = dryrun_metrics.get("monthly_net_profit_avg", 0.0)

        return {
            "price_slippage_pct": round(_pct_diff(bt_price, dr_price), 4),
            "signal_gap_pct": round(_pct_diff(bt_signals, dr_signals), 4),
            "pnl_gap_pct": round(_pct_diff(bt_pnl, dr_pnl), 4),
        }

    def build_comparison_matrix(
        self,
        round_num: int,
        candidate_id: str,
        multi_window_result: dict,
        dryrun_deviation: Optional[dict] = None,
    ) -> dict:
        """
        Build a ComparisonMatrix dict.

        Returns::

            {
                "round": int,
                "candidate_id": str,
                "windows": list[str],
                "metrics_by_window": dict,
                "robustness_score": float,
                "dryrun_price_slippage_pct": float,
                "dryrun_signal_gap_pct": float,
                "dryrun_pnl_gap_pct": float,
            }
        """
        mbw = multi_window_result.get("metrics_by_window", {})
        dev = dryrun_deviation or {}

        return {
            "round": round_num,
            "candidate_id": candidate_id,
            "windows": list(mbw.keys()),
            "metrics_by_window": mbw,
            "robustness_score": multi_window_result.get("robustness_score", 0.0),
            "dryrun_price_slippage_pct": dev.get("price_slippage_pct", 0.0),
            "dryrun_signal_gap_pct": dev.get("signal_gap_pct", 0.0),
            "dryrun_pnl_gap_pct": dev.get("pnl_gap_pct", 0.0),
        }

    def save_matrix(self, matrix: dict):
        """Persist the comparison matrix to ``results/comparisons/comparison_matrix.json``."""
        os.makedirs(self.output_dir, exist_ok=True)
        path = os.path.join(self.output_dir, "comparison_matrix.json")
        with open(path, "w") as f:
            json.dump(matrix, f, indent=2, ensure_ascii=False, default=str)
        logger.info("Comparison matrix saved to %s", path)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _calc_robustness(self, metrics_by_window: dict) -> float:
        """
        Robustness score based on the coefficient of variation (CV) of
        each window's composite score.

        ``robustness = max(0, 100 - CV * 100)``

        A lower CV (more consistent scores) yields a higher robustness value.
        """
        scores: list[float] = []
        for _label, m in metrics_by_window.items():
            if not m:
                continue
            # Use monthly_net_profit_avg as a proxy score if no explicit score
            s = m.get("score", m.get("monthly_net_profit_avg", 0.0))
            scores.append(s)

        if len(scores) < 2:
            return 0.0

        mean = statistics.mean(scores)
        if mean == 0:
            return 0.0

        stdev = statistics.stdev(scores)
        cv = stdev / abs(mean)
        return round(max(0.0, 100.0 - cv * 100.0), 2)
