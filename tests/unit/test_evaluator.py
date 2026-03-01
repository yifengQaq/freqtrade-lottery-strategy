"""
Unit tests for agent.evaluator — gate checks, scoring, walk-forward, improvement.
"""

import pytest

from agent.evaluator import Evaluator, EvalResult, PassCriteria, ScoreWeights


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def make_metrics(**overrides) -> dict:
    """Return a fully-passing metrics dict; override individual fields as needed."""
    base = {
        "total_profit_pct": 80.0,
        "sharpe_ratio": 1.5,
        "win_rate": 0.45,
        "weekly_target_hit_rate": 0.15,
        "max_drawdown_pct": 40.0,
        "total_trades": 100,
        "stake_limit_hit_count": 0,
        "monthly_net_profit_avg": 50.0,
        "max_monthly_loss": 100.0,
        "avg_trade_duration_hours": 24.0,
        "avg_profit_per_trade_pct": 0.5,
    }
    base.update(overrides)
    return base


@pytest.fixture
def evaluator() -> Evaluator:
    return Evaluator()


# ===================================================================
# Gate checks
# ===================================================================


class TestGateChecks:
    """门控检查测试组"""

    def test_gate_all_pass(self, evaluator: Evaluator):
        """全部指标达标 → passed=True, gate_failures=[]"""
        result = evaluator.evaluate(make_metrics())
        assert result.passed is True
        assert result.gate_failures == []

    def test_gate_weekly_target_hit_rate_fail(self, evaluator: Evaluator):
        """周达标率 0.05 < 0.10 → 失败"""
        result = evaluator.evaluate(make_metrics(weekly_target_hit_rate=0.05))
        assert result.passed is False
        assert any("weekly_target_hit_rate" in f for f in result.gate_failures)

    def test_gate_total_trades_fail(self, evaluator: Evaluator):
        """总交易数 30 < 50 → 失败"""
        result = evaluator.evaluate(make_metrics(total_trades=30))
        assert result.passed is False
        assert any("total_trades" in f for f in result.gate_failures)

    def test_gate_stake_limit_hit_fail(self, evaluator: Evaluator):
        """超限次数 > 0 → 失败"""
        result = evaluator.evaluate(make_metrics(stake_limit_hit_count=3))
        assert result.passed is False
        assert any("stake_limit_hit_count" in f for f in result.gate_failures)

    def test_gate_monthly_profit_fail(self, evaluator: Evaluator):
        """月均利润 <= 0 → 失败"""
        result = evaluator.evaluate(make_metrics(monthly_net_profit_avg=-5.0))
        assert result.passed is False
        assert any("monthly_net_profit_avg" in f for f in result.gate_failures)

    def test_gate_monthly_profit_zero_fail(self, evaluator: Evaluator):
        """月均利润 == 0 也应失败（边界值 <= 0）"""
        result = evaluator.evaluate(make_metrics(monthly_net_profit_avg=0.0))
        assert result.passed is False
        assert any("monthly_net_profit_avg" in f for f in result.gate_failures)

    def test_gate_multiple_failures(self, evaluator: Evaluator):
        """多项门控同时失败 → gate_failures 包含多项"""
        result = evaluator.evaluate(
            make_metrics(
                weekly_target_hit_rate=0.05,
                total_trades=30,
                stake_limit_hit_count=2,
            )
        )
        assert result.passed is False
        assert len(result.gate_failures) >= 3
        failure_text = " ".join(result.gate_failures)
        assert "weekly_target_hit_rate" in failure_text
        assert "total_trades" in failure_text
        assert "stake_limit_hit_count" in failure_text

    def test_gate_total_profit_pct_fail(self, evaluator: Evaluator):
        """总收益 < -30% → 直接失败"""
        result = evaluator.evaluate(make_metrics(total_profit_pct=-50.0))
        assert result.passed is False
        assert any("total_profit_pct" in f for f in result.gate_failures)

    def test_gate_total_profit_pct_pass_boundary(self, evaluator: Evaluator):
        """总收益 = -30% 精确边界 → 通过（不低于 -30%）"""
        result = evaluator.evaluate(make_metrics(total_profit_pct=-30.0))
        # -30.0 is NOT < -30.0, so this gate should pass
        assert not any("total_profit_pct" in f for f in result.gate_failures)

    def test_gate_max_drawdown_fail(self, evaluator: Evaluator):
        """最大回撤 > 80% → 失败"""
        result = evaluator.evaluate(make_metrics(max_drawdown_pct=85.0))
        assert result.passed is False
        assert any("max_drawdown_pct" in f for f in result.gate_failures)


# ===================================================================
# Score calculation
# ===================================================================


class TestScoreCalculation:
    """评分计算测试组"""

    def test_score_calculation(self, evaluator: Evaluator):
        """v2 评分公式：给定已知指标，验证 score 与手算结果一致。

        Score =
            80.0 * 0.35                     = 28.0
          + 1.5 * 10 * 0.20                =  3.0
          + 0.45 * 100 * 0.15              =  6.75
          - 40.0 * 0.15                    = -6.0
          + 50.0 * 0.10                    =  5.0
          + (1/24) * 0.05 * 100            ≈  0.2083
          ≈ 36.96
        """
        result = evaluator.evaluate(make_metrics())
        expected = (
            80.0 * 0.35
            + 1.5 * 10 * 0.20
            + 0.45 * 100 * 0.15
            - 40.0 * 0.15
            + 50.0 * 0.10
            + (1 / 24.0) * 0.05 * 100
        )
        assert result.score == pytest.approx(expected, abs=0.01)

    def test_score_zero_duration_protection(self, evaluator: Evaluator):
        """avg_trade_duration_hours=0 时不出异常（除零保护，回退到 24h）"""
        result = evaluator.evaluate(make_metrics(avg_trade_duration_hours=0))
        # 应与 duration=24 结果一致
        baseline = evaluator.evaluate(make_metrics(avg_trade_duration_hours=24))
        assert result.score == pytest.approx(baseline.score, abs=0.01)

    def test_score_negative_duration_protection(self, evaluator: Evaluator):
        """负数 duration 同样触发除零保护"""
        result = evaluator.evaluate(make_metrics(avg_trade_duration_hours=-5))
        baseline = evaluator.evaluate(make_metrics(avg_trade_duration_hours=24))
        assert result.score == pytest.approx(baseline.score, abs=0.01)

    def test_score_breakdown_keys(self, evaluator: Evaluator):
        """score_breakdown 包含各分项"""
        result = evaluator.evaluate(make_metrics())
        expected_keys = {
            "total_profit_component",
            "sharpe_component",
            "win_rate_component",
            "drawdown_penalty",
            "monthly_profit_component",
            "trade_efficiency_component",
        }
        assert set(result.score_breakdown.keys()) == expected_keys

    def test_score_profit_dominates(self, evaluator: Evaluator):
        """高收益策略得分必须高于亏损策略"""
        profit_strategy = evaluator.evaluate(make_metrics(total_profit_pct=200.0))
        loss_strategy = evaluator.evaluate(make_metrics(total_profit_pct=-50.0))
        assert profit_strategy.score > loss_strategy.score

    def test_score_sharpe_contributes(self, evaluator: Evaluator):
        """更高 Sharpe 应该得到更高分数"""
        high_sharpe = evaluator.evaluate(make_metrics(sharpe_ratio=3.0))
        low_sharpe = evaluator.evaluate(make_metrics(sharpe_ratio=0.0))
        assert high_sharpe.score > low_sharpe.score


# ===================================================================
# Recommendation
# ===================================================================


class TestRecommendation:
    """建议生成测试组"""

    def test_recommendation_gate_fail(self, evaluator: Evaluator):
        """门控失败时 recommendation 非空"""
        result = evaluator.evaluate(make_metrics(total_trades=10))
        assert result.recommendation != ""

    def test_recommendation_gate_pass(self, evaluator: Evaluator):
        """门控通过时 recommendation 非空（含策略表现的信息）"""
        result = evaluator.evaluate(make_metrics())
        assert result.recommendation != ""


# ===================================================================
# Walk-Forward (compare_is_oos)
# ===================================================================


class TestWalkForward:
    """Walk-forward 过拟合检测测试组"""

    def test_compare_is_oos_pass(self, evaluator: Evaluator):
        """OOS/IS ratio = 69/100 = 0.69 >= 0.6 → 通过"""
        is_res = EvalResult(passed=True, score=100.0)
        oos_res = EvalResult(passed=True, score=69.0)
        ok, msg = evaluator.compare_is_oos(is_res, oos_res)
        assert ok is True
        assert "PASSED" in msg

    def test_compare_is_oos_fail(self, evaluator: Evaluator):
        """OOS/IS ratio = 50/100 = 0.50 < 0.6 → 失败"""
        is_res = EvalResult(passed=True, score=100.0)
        oos_res = EvalResult(passed=True, score=50.0)
        ok, msg = evaluator.compare_is_oos(is_res, oos_res)
        assert ok is False
        assert "OVERFITTING" in msg

    def test_compare_is_oos_zero_is(self, evaluator: Evaluator):
        """IS score=0 → 无法评估，返回 (False, ...)"""
        is_res = EvalResult(passed=True, score=0.0)
        oos_res = EvalResult(passed=True, score=10.0)
        ok, msg = evaluator.compare_is_oos(is_res, oos_res)
        assert ok is False
        assert "0" in msg


# ===================================================================
# Improvement
# ===================================================================


class TestImprovement:
    """提升判定测试组"""

    def test_is_improvement_true(self, evaluator: Evaluator):
        """score 差 > min_improvement → True"""
        current = EvalResult(passed=True, score=10.0)
        previous = EvalResult(passed=True, score=9.0)
        assert evaluator.is_improvement(current, previous, min_improvement=0.5) is True

    def test_is_improvement_false(self, evaluator: Evaluator):
        """score 差 < min_improvement → False"""
        current = EvalResult(passed=True, score=10.0)
        previous = EvalResult(passed=True, score=9.8)
        assert evaluator.is_improvement(current, previous, min_improvement=0.5) is False

    def test_is_improvement_exact_boundary(self, evaluator: Evaluator):
        """score 差 == min_improvement → False（需 strictly greater）"""
        current = EvalResult(passed=True, score=10.5)
        previous = EvalResult(passed=True, score=10.0)
        assert evaluator.is_improvement(current, previous, min_improvement=0.5) is False
