"""
Evaluator — scores backtest results using OP-strategy-specific criteria.

Implements:
1. Pass/Fail gate checks (from agent_iteration_rules.yaml)
2. Composite scoring formula
3. Walk-forward validation comparison
4. Overfitting detection
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class PassCriteria:
    """Configurable pass/fail thresholds from iteration rules."""
    weekly_target_hit_rate_min: float = 0.10
    max_drawdown_pct_max: float = 80.0
    total_trades_min: int = 50
    stake_limit_hit_count_max: int = 0
    monthly_net_profit_avg_min: float = 0.0
    total_profit_pct_min: float = -30.0   # reject strategies losing > 30%


@dataclass
class ScoreWeights:
    """Weights for composite score calculation.

    v2: profit-centric weights — total_profit_pct is the primary driver so
    that the optimisation loop selects strategies that actually make money.
    """
    total_profit_w: float = 0.35          # total profit % — primary driver
    sharpe_w: float = 0.20                # risk-adjusted returns
    win_rate_w: float = 0.15              # consistency
    max_drawdown_w: float = 0.15          # drawdown penalty
    monthly_avg_profit_w: float = 0.10    # monthly profit consistency
    trade_efficiency_w: float = 0.05      # duration efficiency


@dataclass
class EvalResult:
    """Result of a single evaluation."""
    passed: bool
    score: float
    gate_failures: list[str] = field(default_factory=list)
    score_breakdown: dict = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    is_overfitting: bool = False
    recommendation: str = ""

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "score": round(self.score, 2),
            "gate_failures": self.gate_failures,
            "score_breakdown": {
                k: round(v, 4) for k, v in self.score_breakdown.items()
            },
            "is_overfitting": self.is_overfitting,
            "recommendation": self.recommendation,
        }


class Evaluator:
    """Evaluate backtest results against OP strategy criteria."""

    def __init__(
        self,
        pass_criteria: Optional[PassCriteria] = None,
        score_weights: Optional[ScoreWeights] = None,
        oos_score_ratio_min: float = 0.6,
    ):
        self.criteria = pass_criteria or PassCriteria()
        self.weights = score_weights or ScoreWeights()
        self.oos_score_ratio_min = oos_score_ratio_min

    def evaluate(self, metrics: dict) -> EvalResult:
        """
        Full evaluation: gate checks + scoring.

        Args:
            metrics: Normalized metrics dict from BacktestRunner.

        Returns:
            EvalResult with pass/fail, score, and recommendations.
        """
        gate_failures = self._gate_check(metrics)
        score, breakdown = self._calculate_score(metrics)
        passed = len(gate_failures) == 0

        recommendation = self._generate_recommendation(
            metrics, gate_failures, score
        )

        return EvalResult(
            passed=passed,
            score=score,
            gate_failures=gate_failures,
            score_breakdown=breakdown,
            metrics=metrics,
            recommendation=recommendation,
        )

    def compare_is_oos(
        self,
        is_result: EvalResult,
        oos_result: EvalResult,
    ) -> tuple[bool, str]:
        """
        Compare in-sample vs out-of-sample results for overfitting.

        Returns:
            (is_valid, message)
        """
        if is_result.score == 0:
            return False, "IS score is 0, cannot evaluate"

        ratio = oos_result.score / is_result.score if is_result.score > 0 else 0

        if ratio < self.oos_score_ratio_min:
            return False, (
                f"OVERFITTING: OOS/IS ratio = {ratio:.2f} "
                f"< {self.oos_score_ratio_min} threshold. "
                f"IS score={is_result.score:.2f}, OOS score={oos_result.score:.2f}"
            )

        return True, (
            f"Walk-forward PASSED: OOS/IS ratio = {ratio:.2f} "
            f"(threshold: {self.oos_score_ratio_min})"
        )

    def is_improvement(
        self,
        current: EvalResult,
        previous: EvalResult,
        min_improvement: float = 0.5,
    ) -> bool:
        """Check if current round improved over previous."""
        return current.score > previous.score + min_improvement

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _gate_check(self, m: dict) -> list[str]:
        """Run pass/fail gate checks. Returns list of failure messages."""
        failures = []

        wtr = m.get("weekly_target_hit_rate", 0)
        if wtr < self.criteria.weekly_target_hit_rate_min:
            failures.append(
                f"weekly_target_hit_rate={wtr:.2%} "
                f"< {self.criteria.weekly_target_hit_rate_min:.2%}"
            )

        mdd = m.get("max_drawdown_pct", 0)
        if mdd > self.criteria.max_drawdown_pct_max:
            failures.append(
                f"max_drawdown_pct={mdd:.1f}% "
                f"> {self.criteria.max_drawdown_pct_max}%"
            )

        trades = m.get("total_trades", 0)
        if trades < self.criteria.total_trades_min:
            failures.append(
                f"total_trades={trades} < {self.criteria.total_trades_min}"
            )

        stake_hits = m.get("stake_limit_hit_count", 0)
        if stake_hits > self.criteria.stake_limit_hit_count_max:
            failures.append(
                f"stake_limit_hit_count={stake_hits} "
                f"> {self.criteria.stake_limit_hit_count_max}"
            )

        monthly_avg = m.get("monthly_net_profit_avg", 0)
        if monthly_avg <= self.criteria.monthly_net_profit_avg_min:
            failures.append(
                f"monthly_net_profit_avg={monthly_avg:.2f} "
                f"<= {self.criteria.monthly_net_profit_avg_min}"
            )

        # Total profit floor — reject severely losing strategies
        total_profit = m.get("total_profit_pct", 0)
        if total_profit < self.criteria.total_profit_pct_min:
            failures.append(
                f"total_profit_pct={total_profit:.1f}% "
                f"< {self.criteria.total_profit_pct_min}%"
            )

        return failures

    def _calculate_score(self, m: dict) -> tuple[float, dict]:
        """
        Calculate composite score (v2 — profit-centric).

        Score =
            total_profit_pct * 0.35           ← primary profit driver
          + sharpe_ratio * 10 * 0.20          ← risk-adjusted
          + win_rate * 100 * 0.15             ← consistency
          - max_drawdown_pct * 0.15           ← drawdown penalty
          + monthly_net_profit_avg * 0.10     ← monthly consistency
          + (1 / duration) * 100 * 0.05       ← efficiency
        """
        w = self.weights

        total_profit = m.get("total_profit_pct", 0)
        sharpe = m.get("sharpe_ratio", 0)
        win_rate = m.get("win_rate", 0)
        mdd = m.get("max_drawdown_pct", 0)
        monthly_profit = m.get("monthly_net_profit_avg", 0)
        avg_duration = m.get("avg_trade_duration_hours", 24)

        # Protect against division by zero
        if avg_duration <= 0:
            avg_duration = 24

        s1 = total_profit * w.total_profit_w
        s2 = sharpe * 10 * w.sharpe_w
        s3 = win_rate * 100 * w.win_rate_w
        s4 = -mdd * w.max_drawdown_w
        s5 = monthly_profit * w.monthly_avg_profit_w
        s6 = (1 / avg_duration) * w.trade_efficiency_w * 100

        total = s1 + s2 + s3 + s4 + s5 + s6

        breakdown = {
            "total_profit_component": s1,
            "sharpe_component": s2,
            "win_rate_component": s3,
            "drawdown_penalty": s4,
            "monthly_profit_component": s5,
            "trade_efficiency_component": s6,
        }

        return round(total, 2), breakdown

    def _generate_recommendation(
        self,
        m: dict,
        failures: list[str],
        score: float,
    ) -> str:
        """Generate actionable recommendation based on results."""
        recs = []

        trades = m.get("total_trades", 0)
        total_profit = m.get("total_profit_pct", 0)
        win_rate = m.get("win_rate", 0)
        mdd = m.get("max_drawdown_pct", 0)
        sharpe = m.get("sharpe_ratio", 0)
        avg_profit = m.get("avg_profit_per_trade_pct", 0)
        duration = m.get("avg_trade_duration_hours", 0)

        if trades < 50:
            recs.append(
                f"交易次数不足({trades}), 建议放宽入场条件或增加交易对"
            )

        if total_profit < -50:
            recs.append(
                f"总收益严重亏损({total_profit:.1f}%), 需要根本性重构入场逻辑"
            )
        elif total_profit < 0:
            recs.append(
                f"总收益为负({total_profit:.1f}%), 优化止损和出场参数"
            )

        if win_rate < 0.3 and trades > 50:
            recs.append(
                f"胜率过低({win_rate:.0%}), 收紧入场条件提高信号质量"
            )

        if mdd > 60:
            recs.append(
                f"最大回撤过大({mdd:.1f}%), 降低杠杆或收紧止损"
            )

        if sharpe < 0 and trades > 50:
            recs.append(
                "Sharpe为负, 风险收益比不合理, 考虑改变入场方向或时机"
            )

        if avg_profit < 0:
            recs.append(
                "平均每笔亏损, 信号质量需要根本性改善"
            )

        if duration > 72:
            recs.append(
                f"平均持仓{duration:.0f}小时过长, 考虑缩短时间止损"
            )
        elif duration < 0.5:
            recs.append(
                "持仓时间过短(<30min), 可能频繁被止损扫出"
            )

        if not recs:
            if score > 100:
                recs.append("策略表现优秀, 可尝试微调 trailing stop 参数进一步优化")
            elif score > 30:
                recs.append("策略表现良好, 建议小幅调参精细优化")
            else:
                recs.append("策略中等, 建议调整入场/出场参数组合")

        return " | ".join(recs)
