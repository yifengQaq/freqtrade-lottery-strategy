"""
Unit tests for agent.weekly_settlement — WeeklySettlementManager (US7 T056).

Covers:
1. test_target_hit — 周收益 >= 1000 → TARGET_HIT
2. test_budget_exhausted — 周亏损 >= -100 → BUDGET_EXHAUSTED
3. test_week_end_settled — +300（未达标未亏完）→ WEEK_END_SETTLED
4. test_force_settle_no_carryover — WEEK_END_SETTLED 不允许跨周复利
5. test_cooldown_triggered — 连续 3 周未达标且净值恶化 → cooldown
6. test_cooldown_not_triggered — 连续 2 周未达标 → 不触发
7. test_settlement_report_format — 报告字段完整性
"""

import json
import os

import pytest

from agent.weekly_settlement import WeeklySettlementManager


# ===================================================================
# T056-1: test_target_hit
# ===================================================================


class TestTargetHit:
    """周收益 >= 1000 → status=TARGET_HIT, action=reset_budget_100."""

    def test_target_hit(self):
        mgr = WeeklySettlementManager(weekly_budget=100.0, weekly_target=1000.0)
        report = mgr.settle_week("2026-W09", weekly_pnl=1200.0)

        assert report["status"] == "TARGET_HIT"
        assert report["reached_target"] is True
        assert report["exhausted_budget"] is False
        assert report["action_next_week"] == "reset_budget_100"
        assert report["cooldown_triggered"] is False

    def test_target_hit_exact_boundary(self):
        """Exactly at target threshold."""
        mgr = WeeklySettlementManager(weekly_budget=100.0, weekly_target=1000.0)
        report = mgr.settle_week("2026-W09", weekly_pnl=1000.0)

        assert report["status"] == "TARGET_HIT"
        assert report["reached_target"] is True


# ===================================================================
# T056-2: test_budget_exhausted
# ===================================================================


class TestBudgetExhausted:
    """周亏损 >= -100 → status=BUDGET_EXHAUSTED, action=reset_budget_100."""

    def test_budget_exhausted(self):
        mgr = WeeklySettlementManager(weekly_budget=100.0, weekly_target=1000.0)
        report = mgr.settle_week("2026-W09", weekly_pnl=-100.0)

        assert report["status"] == "BUDGET_EXHAUSTED"
        assert report["reached_target"] is False
        assert report["exhausted_budget"] is True
        assert report["action_next_week"] == "reset_budget_100"
        assert report["cooldown_triggered"] is False

    def test_budget_exhausted_deep_loss(self):
        """Loss deeper than budget."""
        mgr = WeeklySettlementManager(weekly_budget=100.0, weekly_target=1000.0)
        report = mgr.settle_week("2026-W09", weekly_pnl=-250.0)

        assert report["status"] == "BUDGET_EXHAUSTED"
        assert report["exhausted_budget"] is True


# ===================================================================
# T056-3: test_week_end_settled
# ===================================================================


class TestWeekEndSettled:
    """周收益 +300（未达标未亏完）→ WEEK_END_SETTLED, reset_budget_100."""

    def test_week_end_settled(self):
        mgr = WeeklySettlementManager(weekly_budget=100.0, weekly_target=1000.0)
        report = mgr.settle_week("2026-W09", weekly_pnl=300.0)

        assert report["status"] == "WEEK_END_SETTLED"
        assert report["reached_target"] is False
        assert report["exhausted_budget"] is False
        assert report["action_next_week"] == "reset_budget_100"
        assert report["cooldown_triggered"] is False

    def test_week_end_settled_small_loss(self):
        """Small loss that doesn't exhaust budget."""
        mgr = WeeklySettlementManager(weekly_budget=100.0, weekly_target=1000.0)
        report = mgr.settle_week("2026-W09", weekly_pnl=-50.0)

        assert report["status"] == "WEEK_END_SETTLED"
        assert report["exhausted_budget"] is False


# ===================================================================
# T056-4: test_force_settle_no_carryover
# ===================================================================


class TestForceSettleNoCarryover:
    """WEEK_END_SETTLED 不允许跨周复利 — action 始终是 reset_budget_100。"""

    def test_force_settle_no_carryover(self):
        mgr = WeeklySettlementManager(weekly_budget=100.0, weekly_target=1000.0)

        # Week 1: profit +500 (not hitting target)
        r1 = mgr.settle_week("2026-W08", weekly_pnl=500.0)
        assert r1["status"] == "WEEK_END_SETTLED"
        assert r1["action_next_week"] == "reset_budget_100"

        # Week 2: profit +800 (still not target)
        r2 = mgr.settle_week("2026-W09", weekly_pnl=800.0)
        assert r2["status"] == "WEEK_END_SETTLED"
        # Even after profitable weeks, action is reset — no carry-over
        assert r2["action_next_week"] == "reset_budget_100"

    def test_no_carryover_means_fixed_budget(self):
        """Each week resets to the same budget — profit doesn't compound."""
        mgr = WeeklySettlementManager(weekly_budget=100.0, weekly_target=1000.0)

        for wk in range(1, 6):
            r = mgr.settle_week(f"2026-W{wk:02d}", weekly_pnl=200.0)
            # Every single week: same reset action, no compounding
            assert r["action_next_week"] == "reset_budget_100"
            assert r["status"] == "WEEK_END_SETTLED"


# ===================================================================
# T056-5: test_cooldown_triggered
# ===================================================================


class TestCooldownTriggered:
    """连续 3 周未达标且净值恶化 → cooldown_triggered=True."""

    def test_cooldown_triggered(self):
        mgr = WeeklySettlementManager(
            weekly_budget=100.0,
            weekly_target=1000.0,
            cooldown_threshold_weeks=3,
        )

        # Three consecutive losing weeks (status != TARGET_HIT, pnl < 0)
        mgr.settle_week("2026-W07", weekly_pnl=-30.0)
        mgr.settle_week("2026-W08", weekly_pnl=-50.0)
        r3 = mgr.settle_week("2026-W09", weekly_pnl=-20.0)

        assert r3["cooldown_triggered"] is True
        assert r3["action_next_week"] == "cooldown_dryrun"

    def test_cooldown_with_budget_exhausted_weeks(self):
        """BUDGET_EXHAUSTED weeks also count as not hitting target."""
        mgr = WeeklySettlementManager(
            weekly_budget=100.0,
            weekly_target=1000.0,
            cooldown_threshold_weeks=3,
        )

        mgr.settle_week("2026-W07", weekly_pnl=-100.0)  # BUDGET_EXHAUSTED
        mgr.settle_week("2026-W08", weekly_pnl=-80.0)    # WEEK_END_SETTLED
        r3 = mgr.settle_week("2026-W09", weekly_pnl=-40.0)  # WEEK_END_SETTLED

        assert r3["cooldown_triggered"] is True
        assert r3["action_next_week"] == "cooldown_dryrun"


# ===================================================================
# T056-6: test_cooldown_not_triggered
# ===================================================================


class TestCooldownNotTriggered:
    """连续 2 周未达标 → 未满 3 周阈值，不触发冷却。"""

    def test_cooldown_not_triggered_insufficient_weeks(self):
        mgr = WeeklySettlementManager(
            weekly_budget=100.0,
            weekly_target=1000.0,
            cooldown_threshold_weeks=3,
        )

        mgr.settle_week("2026-W08", weekly_pnl=-30.0)
        r2 = mgr.settle_week("2026-W09", weekly_pnl=-50.0)

        assert r2["cooldown_triggered"] is False
        assert r2["action_next_week"] == "reset_budget_100"

    def test_cooldown_not_triggered_one_target_hit(self):
        """If one of the last 3 weeks hit target, no cooldown."""
        mgr = WeeklySettlementManager(
            weekly_budget=100.0,
            weekly_target=1000.0,
            cooldown_threshold_weeks=3,
        )

        mgr.settle_week("2026-W07", weekly_pnl=-30.0)
        mgr.settle_week("2026-W08", weekly_pnl=1200.0)  # TARGET_HIT breaks streak
        r3 = mgr.settle_week("2026-W09", weekly_pnl=-20.0)

        assert r3["cooldown_triggered"] is False

    def test_cooldown_not_triggered_positive_pnl(self):
        """3 weeks miss target but pnl positive → no cooldown (no deterioration)."""
        mgr = WeeklySettlementManager(
            weekly_budget=100.0,
            weekly_target=1000.0,
            cooldown_threshold_weeks=3,
        )

        mgr.settle_week("2026-W07", weekly_pnl=100.0)
        mgr.settle_week("2026-W08", weekly_pnl=200.0)
        r3 = mgr.settle_week("2026-W09", weekly_pnl=50.0)

        # All miss target but pnl > 0 → not deteriorating
        assert r3["cooldown_triggered"] is False


# ===================================================================
# T056-7: test_settlement_report_format
# ===================================================================


class TestSettlementReportFormat:
    """验证报告包含所有必需字段。"""

    REQUIRED_KEYS = {
        "week_id",
        "status",
        "weekly_pnl",
        "reached_target",
        "exhausted_budget",
        "action_next_week",
        "cooldown_triggered",
    }

    def test_settlement_report_format(self):
        mgr = WeeklySettlementManager()
        report = mgr.settle_week("2026-W09", weekly_pnl=300.0)

        assert isinstance(report, dict)
        assert self.REQUIRED_KEYS.issubset(report.keys()), (
            f"Missing keys: {self.REQUIRED_KEYS - report.keys()}"
        )

    def test_report_values_types(self):
        mgr = WeeklySettlementManager()
        report = mgr.settle_week("2026-W09", weekly_pnl=-50.0)

        assert isinstance(report["week_id"], str)
        assert isinstance(report["status"], str)
        assert isinstance(report["weekly_pnl"], float)
        assert isinstance(report["reached_target"], bool)
        assert isinstance(report["exhausted_budget"], bool)
        assert isinstance(report["action_next_week"], str)
        assert isinstance(report["cooldown_triggered"], bool)

    def test_status_is_valid_enum(self):
        mgr = WeeklySettlementManager()
        valid_statuses = {"TARGET_HIT", "BUDGET_EXHAUSTED", "WEEK_END_SETTLED"}

        for pnl in [1500.0, -100.0, 200.0]:
            r = WeeklySettlementManager().settle_week("2026-W09", weekly_pnl=pnl)
            assert r["status"] in valid_statuses


# ===================================================================
# Persistence tests
# ===================================================================


class TestPersistence:
    """save_report / load_history round-trip."""

    def test_save_and_load(self, tmp_path):
        path = str(tmp_path / "reports.jsonl")
        mgr = WeeklySettlementManager(report_path=path)

        r1 = mgr.settle_week("2026-W08", weekly_pnl=300.0)
        mgr.save_report(r1)

        r2 = mgr.settle_week("2026-W09", weekly_pnl=-50.0)
        mgr.save_report(r2)

        # New manager, load from file
        mgr2 = WeeklySettlementManager(report_path=path)
        mgr2.load_history()

        assert len(mgr2.get_history()) == 2
        assert mgr2.get_history()[0]["week_id"] == "2026-W08"
        assert mgr2.get_history()[1]["week_id"] == "2026-W09"

    def test_load_empty_file(self, tmp_path):
        path = str(tmp_path / "empty.jsonl")
        mgr = WeeklySettlementManager(report_path=path)
        mgr.load_history()  # file doesn't exist → no error
        assert mgr.get_history() == []

    def test_save_creates_directory(self, tmp_path):
        path = str(tmp_path / "nested" / "dir" / "reports.jsonl")
        mgr = WeeklySettlementManager(report_path=path)
        report = mgr.settle_week("2026-W09", weekly_pnl=100.0)
        mgr.save_report(report)

        assert os.path.exists(path)
        with open(path) as f:
            data = json.loads(f.readline())
        assert data["week_id"] == "2026-W09"
