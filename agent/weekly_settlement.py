"""
WeeklySettlementManager — 周结算与未达标周处理 (US7).

三态判定:
1. weekly_pnl >= weekly_target → TARGET_HIT
2. weekly_pnl <= -weekly_budget → BUDGET_EXHAUSTED
3. else → WEEK_END_SETTLED（强制结算，无跨周复利）

冷却机制:
- 连续 N 周未达标（status != TARGET_HIT）且净值恶化（pnl 均为负）
  → cooldown_triggered=True, action="cooldown_dryrun"
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class WeeklySettlementManager:
    """Settle a trading week and decide what happens next."""

    def __init__(
        self,
        weekly_budget: float = 100.0,
        weekly_target: float = 1000.0,
        cooldown_threshold_weeks: int = 3,
        report_path: str = "results/weekly/weekly_settlement_reports.jsonl",
    ):
        self.weekly_budget = weekly_budget
        self.weekly_target = weekly_target
        self.cooldown_threshold = cooldown_threshold_weeks
        self.report_path = report_path
        self.history: list[dict] = []  # 历史结算记录

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def settle_week(self, week_id: str, weekly_pnl: float) -> dict:
        """
        结算一周，返回 ``WeeklySettlementReport``。

        三态判定:
        1. weekly_pnl >= weekly_target → TARGET_HIT
        2. weekly_pnl <= -weekly_budget → BUDGET_EXHAUSTED
        3. else → WEEK_END_SETTLED（强制结算，不允许跨周复利）

        Returns:
            WeeklySettlementReport dict.
        """
        reached_target = weekly_pnl >= self.weekly_target
        exhausted_budget = weekly_pnl <= -self.weekly_budget

        if reached_target:
            status = "TARGET_HIT"
        elif exhausted_budget:
            status = "BUDGET_EXHAUSTED"
        else:
            status = "WEEK_END_SETTLED"

        # Append to history *before* cooldown check so current week counts
        report: dict = {
            "week_id": week_id,
            "status": status,
            "weekly_pnl": weekly_pnl,
            "reached_target": reached_target,
            "exhausted_budget": exhausted_budget,
            "action_next_week": "reset_budget_100",
            "cooldown_triggered": False,
        }
        self.history.append(report)

        # Cooldown check
        if self._check_cooldown():
            report["cooldown_triggered"] = True
            report["action_next_week"] = "cooldown_dryrun"

        return report

    def save_report(self, report: dict):
        """追加一条报告到 JSONL 文件。"""
        os.makedirs(os.path.dirname(self.report_path) or ".", exist_ok=True)
        with open(self.report_path, "a") as f:
            f.write(json.dumps(report, ensure_ascii=False, default=str) + "\n")
        logger.info("Settlement report saved to %s", self.report_path)

    def get_history(self) -> list[dict]:
        """返回内存中的历史结算记录。"""
        return list(self.history)

    def load_history(self):
        """从 JSONL 文件加载历史到 ``self.history``。"""
        if not os.path.exists(self.report_path):
            return
        self.history = []
        with open(self.report_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    self.history.append(json.loads(line))
        logger.info("Loaded %d settlement records from %s", len(self.history), self.report_path)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _check_cooldown(self) -> bool:
        """
        连续 N 周未达标（status != TARGET_HIT）且净值恶化（pnl 均为负）
        → 触发冷却。
        """
        n = self.cooldown_threshold
        if len(self.history) < n:
            return False

        tail = self.history[-n:]
        all_miss = all(r["status"] != "TARGET_HIT" for r in tail)
        all_negative = all(r["weekly_pnl"] < 0 for r in tail)
        return all_miss and all_negative
