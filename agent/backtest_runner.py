"""
Backtest Runner — executes freqtrade backtesting and captures results.

Responsibilities:
1. Invoke `freqtrade backtesting` as a subprocess
2. Parse the JSON result file
3. Return a normalized metrics dict for the evaluator
"""

import json
import logging
import os
import subprocess
import glob
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default paths
DEFAULT_FREQTRADE_DIR = os.environ.get("FREQTRADE_DIR", "/opt/freqtrade")
DEFAULT_USER_DATA = os.environ.get("FREQTRADE_USER_DATA", "user_data")
DEFAULT_RESULTS_DIR = os.path.join(DEFAULT_USER_DATA, "backtest_results")
DEFAULT_FREQTRADE_BIN = os.environ.get("FREQTRADE_BIN", "freqtrade")


class BacktestRunner:
    """Run freqtrade backtesting and parse results."""

    def __init__(
        self,
        freqtrade_dir: str = DEFAULT_FREQTRADE_DIR,
        user_data: str = DEFAULT_USER_DATA,
        results_dir: str = DEFAULT_RESULTS_DIR,
        config_path: Optional[str] = None,
        strategy_name: str = "LotteryMindsetStrategy",
        timerange: Optional[str] = None,
        extra_args: Optional[list[str]] = None,
        freqtrade_bin: str = DEFAULT_FREQTRADE_BIN,
    ):
        self.freqtrade_dir = freqtrade_dir
        self.user_data = user_data
        self.results_dir = results_dir
        self.config_path = config_path or os.path.join("config", "config_backtest.json")
        self.strategy_name = strategy_name
        self.timerange = timerange
        self.extra_args = extra_args or []
        self.freqtrade_bin = freqtrade_bin

    def run(
        self,
        strategy_file: Optional[str] = None,
        timerange: Optional[str] = None,
        config_override: Optional[str] = None,
    ) -> dict:
        """
        Execute backtest and return parsed results.

        Returns dict with keys:
            - raw_results: full JSON from freqtrade
            - metrics: normalized metrics dict
            - result_file: path to result JSON
            - success: bool
            - error: str (if failed)
        """
        tr = timerange or self.timerange
        cfg = config_override or self.config_path

        cmd = self._build_command(cfg, tr)
        logger.info("Running backtest: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,  # 10-minute timeout
                cwd=self.freqtrade_dir,
            )

            if result.returncode != 0:
                logger.error("Backtest failed:\nSTDOUT: %s\nSTDERR: %s",
                             result.stdout[-2000:], result.stderr[-2000:])
                return {
                    "success": False,
                    "error": result.stderr[-2000:],
                    "raw_results": {},
                    "metrics": {},
                    "result_file": "",
                }

            # Parse the latest result file
            result_file = self._find_latest_result()
            if not result_file:
                return {
                    "success": False,
                    "error": "No result file found after backtest",
                    "raw_results": {},
                    "metrics": {},
                    "result_file": "",
                }

            raw = self._load_result(result_file)
            metrics = self._extract_metrics(raw)

            return {
                "success": True,
                "error": "",
                "raw_results": raw,
                "metrics": metrics,
                "result_file": result_file,
            }

        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "error": "Backtest timed out (>600s)",
                "raw_results": {},
                "metrics": {},
                "result_file": "",
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "raw_results": {},
                "metrics": {},
                "result_file": "",
            }

    def run_hyperopt(
        self,
        epochs: int = 100,
        spaces: str = "roi stoploss trailing",
        loss_function: str = "SharpeHyperOptLossDaily",
        timerange: Optional[str] = None,
    ) -> dict:
        """Run hyperopt parameter optimization."""
        tr = timerange or self.timerange

        cmd = [
            self.freqtrade_bin, "hyperopt",
            "--config", self.config_path,
            "--userdir", self.user_data,
            "--strategy", self.strategy_name,
            "--hyperopt-loss", loss_function,
            "--epochs", str(epochs),
            "--spaces", *spaces.split(),
        ]
        if tr:
            cmd.extend(["--timerange", tr])
        cmd.extend(self.extra_args)

        logger.info("Running hyperopt: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,  # 1-hour timeout
                cwd=self.freqtrade_dir,
            )

            return {
                "success": result.returncode == 0,
                "stdout": result.stdout[-5000:],
                "stderr": result.stderr[-2000:],
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "stdout": "", "stderr": "Hyperopt timed out"}

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _build_command(self, config: str, timerange: Optional[str]) -> list[str]:
        """Build the freqtrade backtesting command."""
        cmd = [
            self.freqtrade_bin, "backtesting",
            "--config", config,
            "--userdir", self.user_data,
            "--strategy", self.strategy_name,
            "--export", "trades",
            "--export-filename",
            os.path.join(self.results_dir, "backtest-result.json"),
        ]
        if timerange:
            cmd.extend(["--timerange", timerange])
        cmd.extend(self.extra_args)
        return cmd

    def _find_latest_result(self) -> Optional[str]:
        """Find the most recent backtest result file (zip or json)."""
        # Prefer .last_result.json meta file (most reliable)
        meta_path = os.path.join(
            self.freqtrade_dir, self.results_dir, ".last_result.json"
        )
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
            latest = meta.get("latest_backtest")
            if latest:
                full = os.path.join(
                    self.freqtrade_dir, self.results_dir, latest
                )
                if os.path.exists(full):
                    return full

        # Fallback: glob for zip or json
        for ext in ("*.zip", "*.json"):
            pattern = os.path.join(
                self.freqtrade_dir, self.results_dir, f"backtest-result*{ext}"
            )
            files = [f for f in glob.glob(pattern) if not f.endswith('.meta.json')]
            if files:
                return max(files, key=os.path.getmtime)

        return None

    def _load_result(self, path: str) -> dict:
        """Load and return the backtest result JSON (supports zip and plain json)."""
        if path.endswith(".zip"):
            with zipfile.ZipFile(path) as zf:
                json_names = [n for n in zf.namelist() if n.endswith(".json") and "config" not in n]
                if not json_names:
                    return {}
                return json.loads(zf.read(json_names[0]))
        with open(path) as f:
            return json.load(f)

    @staticmethod
    def _extract_metrics(raw: dict) -> dict:
        """
        Extract normalized metrics from freqtrade backtest result JSON.

        Freqtrade result structure:
        {
            "strategy": {
                "StrategyName": {
                    "trades": [...],
                    "results_per_pair": [...],
                    ...
                }
            },
            "strategy_comparison": [...]
        }
        """
        metrics = {}

        try:
            # Get first strategy's results
            strategies = raw.get("strategy", {})
            if not strategies:
                return {"error": "No strategy results found"}

            strat_name = list(strategies.keys())[0]
            strat = strategies[strat_name]

            # Core metrics
            metrics["total_profit_pct"] = strat.get("profit_total", 0) * 100
            metrics["total_profit_abs"] = strat.get("profit_total_abs", 0)
            # freqtrade 2026: max_drawdown_account (ratio), older: max_drawdown
            dd_ratio = strat.get("max_drawdown_account", strat.get("max_drawdown", 0))
            metrics["max_drawdown_pct"] = dd_ratio * 100
            metrics["max_drawdown_abs"] = strat.get("max_drawdown_abs", 0)
            metrics["sharpe_ratio"] = strat.get("sharpe", 0)
            metrics["sortino_ratio"] = strat.get("sortino", 0)
            metrics["profit_factor"] = strat.get("profit_factor", 0)
            metrics["calmar_ratio"] = strat.get("calmar", 0)
            metrics["sqn"] = strat.get("sqn", 0)
            metrics["cagr"] = strat.get("cagr", 0)
            metrics["expectancy"] = strat.get("expectancy", 0)
            metrics["expectancy_ratio"] = strat.get("expectancy_ratio", 0)
            # win_rate: prefer strategy_comparison.winrate → strat.winrate → strat.win_rate
            comparison = raw.get("strategy_comparison", [])
            if comparison and comparison[0].get("winrate") is not None:
                metrics["win_rate"] = comparison[0]["winrate"]
            elif strat.get("winrate") is not None:
                metrics["win_rate"] = strat["winrate"]
            else:
                metrics["win_rate"] = strat.get("win_rate", 0)
            metrics["total_trades"] = strat.get("total_trades", 0)
            metrics["trade_count_long"] = strat.get("trade_count_long", 0)
            metrics["trade_count_short"] = strat.get("trade_count_short", 0)
            metrics["avg_profit_per_trade_pct"] = strat.get(
                "profit_mean", 0
            ) * 100
            metrics["avg_trade_duration"] = strat.get(
                "duration_avg", strat.get("holding_avg", "unknown")
            )
            metrics["backtest_days"] = strat.get("backtest_days", 0)
            metrics["market_change"] = strat.get("market_change", 0)
            metrics["best_pair"] = strat.get("best_pair", "")
            metrics["worst_pair"] = strat.get("worst_pair", "")

            # Trade-level analysis
            trades = strat.get("trades", [])
            metrics["total_trades_count"] = len(trades)

            if trades:
                # Calculate stake limit hits
                stake_hits = sum(
                    1 for t in trades
                    if t.get("stake_amount_error", False)
                )
                metrics["stake_limit_hit_count"] = stake_hits

                # Calculate weekly target hit rate (OP-specific)
                metrics.update(
                    BacktestRunner._calc_weekly_metrics(trades)
                )

                # Average trade duration in hours
                durations = [
                    t.get("trade_duration", 0) for t in trades
                    if t.get("trade_duration")
                ]
                if durations:
                    metrics["avg_trade_duration_hours"] = (
                        sum(durations) / len(durations) / 60
                    )
                else:
                    metrics["avg_trade_duration_hours"] = 0

            else:
                metrics["stake_limit_hit_count"] = 0
                metrics["weekly_target_hit_rate"] = 0
                metrics["avg_trade_duration_hours"] = 0

        except Exception as e:
            logger.error("Failed to extract metrics: %s", e)
            metrics["extraction_error"] = str(e)

        return metrics

    @staticmethod
    def _calc_weekly_metrics(trades: list[dict]) -> dict:
        """
        Calculate OP-strategy-specific weekly metrics.

        Groups trades by ISO week and checks if weekly P&L >= target.
        """
        from collections import defaultdict

        weekly_pnl = defaultdict(float)
        weekly_budget = 100.0
        weekly_target = 1000.0

        for trade in trades:
            # Parse close date
            close_date_str = trade.get("close_date", "")
            if not close_date_str:
                continue
            try:
                if "T" in close_date_str:
                    close_date = datetime.fromisoformat(
                        close_date_str.replace("Z", "+00:00")
                    )
                else:
                    close_date = datetime.strptime(
                        close_date_str, "%Y-%m-%d %H:%M:%S"
                    )
                week_key = close_date.isocalendar()[:2]  # (year, week)
                profit = trade.get("profit_abs", 0)
                weekly_pnl[week_key] += profit
            except (ValueError, TypeError):
                continue

        if not weekly_pnl:
            return {
                "weekly_target_hit_rate": 0,
                "total_weeks": 0,
                "target_hit_weeks": 0,
                "monthly_net_profit_avg": 0,
                "max_monthly_loss": 0,
            }

        total_weeks = len(weekly_pnl)
        target_hit_weeks = sum(
            1 for pnl in weekly_pnl.values() if pnl >= weekly_target
        )
        weekly_target_hit_rate = target_hit_weeks / total_weeks if total_weeks > 0 else 0

        # Monthly aggregation
        from collections import defaultdict as dd
        monthly_pnl = dd(float)
        for (year, week), pnl in weekly_pnl.items():
            month_approx = (week - 1) // 4 + 1  # Rough month
            monthly_pnl[(year, month_approx)] += pnl

        monthly_values = list(monthly_pnl.values())
        monthly_avg = sum(monthly_values) / len(monthly_values) if monthly_values else 0
        max_monthly_loss = abs(min(monthly_values)) if monthly_values else 0

        return {
            "weekly_target_hit_rate": round(weekly_target_hit_rate, 4),
            "total_weeks": total_weeks,
            "target_hit_weeks": target_hit_weeks,
            "monthly_net_profit_avg": round(monthly_avg, 2),
            "max_monthly_loss": round(max_monthly_loss, 2),
        }
