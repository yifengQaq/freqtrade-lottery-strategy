"""
Unit tests for agent.target_optimizer — gap vector, weighted norm,
mode switching, step-size control, and JSONL logging.
"""

import json
import math

import pytest

from agent.target_optimizer import TargetOptimizer, DEFAULT_TARGET_PROFILE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _metrics_at_target() -> dict:
    """Metrics that exactly match the default target profile."""
    return {
        "weekly_target_hit_rate": 0.25,
        "monthly_net_profit_avg": 100.0,
        "max_monthly_loss": 200.0,
        "max_drawdown_pct": 50.0,
    }


def _metrics_below_target() -> dict:
    """Metrics clearly below target (large gap)."""
    return {
        "weekly_target_hit_rate": 0.10,
        "monthly_net_profit_avg": 30.0,
        "max_monthly_loss": 350.0,
        "max_drawdown_pct": 80.0,
    }


def _metrics_near_target() -> dict:
    """Metrics almost at target (small gap)."""
    return {
        "weekly_target_hit_rate": 0.24,
        "monthly_net_profit_avg": 98.0,
        "max_monthly_loss": 205.0,
        "max_drawdown_pct": 51.0,
    }


# ===================================================================
# T048-1: test_compute_gap_vector
# ===================================================================


class TestComputeGapVector:
    """计算当前指标与目标的差距向量。"""

    def test_compute_gap_vector(self):
        opt = TargetOptimizer()
        gap = opt.compute_gap(_metrics_below_target(), round_num=1)

        assert gap["round"] == 1
        assert gap["target_profile"] == "default"
        assert "deltas" in gap
        assert "weighted_norm" in gap
        assert "mode" in gap

        # weekly_target_hit_rate: target=0.25, current=0.10 → delta = +0.15
        assert gap["deltas"]["weekly_target_hit_rate"] == pytest.approx(0.15, abs=1e-4)
        # monthly_net_profit_avg: target=100, current=30 → delta = +70
        assert gap["deltas"]["monthly_net_profit_avg"] == pytest.approx(70.0, abs=1e-4)
        # max_monthly_loss: target=200, current=350 → delta = +150 (over limit)
        assert gap["deltas"]["max_monthly_loss"] == pytest.approx(150.0, abs=1e-4)
        # max_drawdown_pct: target=50, current=80 → delta = +30 (over limit)
        assert gap["deltas"]["max_drawdown_pct"] == pytest.approx(30.0, abs=1e-4)

    def test_gap_at_target(self):
        """When metrics match target → all deltas ≤ 0, weighted_norm ≈ 0."""
        opt = TargetOptimizer()
        gap = opt.compute_gap(_metrics_at_target(), round_num=5)

        assert gap["weighted_norm"] == pytest.approx(0.0, abs=1e-6)
        for delta in gap["deltas"].values():
            assert delta <= 0.0 + 1e-9


# ===================================================================
# T048-2: test_weighted_norm
# ===================================================================


class TestWeightedNorm:
    """加权范数计算。"""

    def test_weighted_norm(self):
        opt = TargetOptimizer()
        # Manually check: only positive deltas contribute
        deltas = {"weekly_target_hit_rate": 0.15, "monthly_net_profit_avg": 70.0,
                  "max_monthly_loss": 150.0, "max_drawdown_pct": 30.0}
        norm = opt._weighted_norm(deltas)
        assert norm > 0.0

    def test_weighted_norm_all_met(self):
        """All deltas ≤ 0 → norm = 0."""
        opt = TargetOptimizer()
        deltas = {"weekly_target_hit_rate": -0.05, "monthly_net_profit_avg": -20.0,
                  "max_monthly_loss": -50.0, "max_drawdown_pct": -10.0}
        assert opt._weighted_norm(deltas) == 0.0


# ===================================================================
# T048-3: test_fine_tune_mode_switch
# ===================================================================


class TestFineTuneModeSwitch:
    """weighted_norm < threshold → mode = "fine_tune"。"""

    def test_fine_tune_mode_switch(self):
        # Use very high threshold so even a small gap triggers fine_tune
        opt = TargetOptimizer(fine_tune_threshold=100.0)
        gap = opt.compute_gap(_metrics_near_target(), round_num=3)
        assert gap["mode"] == "fine_tune"

    def test_fine_tune_exactly_at_target(self):
        opt = TargetOptimizer()
        gap = opt.compute_gap(_metrics_at_target(), round_num=1)
        # norm = 0 < any positive threshold → fine_tune
        assert gap["mode"] == "fine_tune"


# ===================================================================
# T048-4: test_explore_mode
# ===================================================================


class TestExploreMode:
    """weighted_norm >= threshold → mode = "explore"。"""

    def test_explore_mode(self):
        opt = TargetOptimizer(fine_tune_threshold=0.01)
        gap = opt.compute_gap(_metrics_below_target(), round_num=2)
        assert gap["mode"] == "explore"
        assert gap["weighted_norm"] >= 0.01


# ===================================================================
# T048-5: test_step_size_control
# ===================================================================


class TestStepSizeControl:
    """fine_tune 模式下步长更小。"""

    def test_step_size_control(self):
        opt = TargetOptimizer()
        fine_gap = {"mode": "fine_tune"}
        explore_gap = {"mode": "explore"}

        fine_steps = opt.suggest_step_sizes(fine_gap)
        explore_steps = opt.suggest_step_sizes(explore_gap)

        assert fine_steps["max_param_changes"] < explore_steps["max_param_changes"]
        assert fine_steps["step_scale"] < explore_steps["step_scale"]

    def test_fine_tune_step_values(self):
        opt = TargetOptimizer()
        s = opt.suggest_step_sizes({"mode": "fine_tune"})
        assert s["max_param_changes"] == 1
        assert s["step_scale"] == 0.1

    def test_explore_step_values(self):
        opt = TargetOptimizer()
        s = opt.suggest_step_sizes({"mode": "explore"})
        assert s["max_param_changes"] == 3
        assert s["step_scale"] == 1.0


# ===================================================================
# T048-6: test_generate_adjustment (via compute_gap + suggest_step_sizes)
# ===================================================================


class TestGenerateAdjustment:
    """生成参数调整方向和步长。"""

    def test_generate_adjustment(self):
        opt = TargetOptimizer()
        gap = opt.compute_gap(_metrics_below_target(), round_num=1)
        steps = opt.suggest_step_sizes(gap)

        # gap should contain direction info via deltas (positive = needs increase)
        assert gap["deltas"]["monthly_net_profit_avg"] > 0  # need to increase

        # steps should reflect explore mode for far-from-target metrics
        assert steps["max_param_changes"] >= 1
        assert steps["step_scale"] > 0.0


# ===================================================================
# JSONL logging
# ===================================================================


class TestGapLogging:
    """log_gap 追加到 JSONL 日志。"""

    def test_log_gap(self, tmp_path):
        log_path = str(tmp_path / "gap_history.jsonl")
        opt = TargetOptimizer(log_path=log_path)

        gap1 = opt.compute_gap(_metrics_below_target(), round_num=1)
        opt.log_gap(gap1)
        gap2 = opt.compute_gap(_metrics_near_target(), round_num=2)
        opt.log_gap(gap2)

        lines = (tmp_path / "gap_history.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2

        parsed = [json.loads(line) for line in lines]
        assert parsed[0]["round"] == 1
        assert parsed[1]["round"] == 2
