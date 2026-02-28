"""
TargetOptimizer — target-gap-driven parameter tuning advisor.

Responsibilities:
1. Compute the gap between current metrics and the target profile
2. Derive a weighted norm that measures overall distance to target
3. Switch between ``explore`` (large steps) and ``fine_tune`` (small steps)
4. Suggest step sizes for the LLM to use when proposing parameter changes
5. Log gap history for offline analysis
"""

import json
import logging
import math
import os
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_TARGET_PROFILE: dict = {
    "weekly_target_hit_rate": 0.25,
    "monthly_net_profit_avg": 100.0,
    "max_monthly_loss": 200.0,
    "max_drawdown_pct": 50.0,
}

# Default weights per metric (higher → more important in weighted norm)
DEFAULT_WEIGHTS: dict[str, float] = {
    "weekly_target_hit_rate": 2.0,
    "monthly_net_profit_avg": 1.5,
    "max_monthly_loss": 1.0,
    "max_drawdown_pct": 1.0,
}


class TargetOptimizer:
    """Compute target–current gap and recommend exploration mode / step sizes."""

    def __init__(
        self,
        target_profile: Optional[dict] = None,
        fine_tune_threshold: float = 0.3,
        log_path: str = "results/comparisons/target_gap_history.jsonl",
        weights: Optional[dict[str, float]] = None,
    ):
        self.target_profile = target_profile or dict(DEFAULT_TARGET_PROFILE)
        self.fine_tune_threshold = fine_tune_threshold
        self.log_path = log_path
        self.weights = weights or dict(DEFAULT_WEIGHTS)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute_gap(self, current_metrics: dict, round_num: int) -> dict:
        """
        Compute the TargetGapVector.

        Returns::

            {
                "round": int,
                "target_profile": "default",
                "deltas": {"weekly_target_hit_rate": -0.05, ...},
                "weighted_norm": float,
                "mode": "explore" | "fine_tune",
            }
        """
        deltas: dict[str, float] = {}
        for key, target_val in self.target_profile.items():
            current_val = current_metrics.get(key, 0.0)
            # For max_monthly_loss & max_drawdown_pct, *lower* is better.
            # delta > 0 means we are worse than target (over the limit).
            if key in ("max_monthly_loss", "max_drawdown_pct"):
                deltas[key] = current_val - target_val  # positive = bad
            else:
                deltas[key] = target_val - current_val  # positive = shortfall

        weighted_norm = self._weighted_norm(deltas)
        mode = (
            "fine_tune" if weighted_norm < self.fine_tune_threshold else "explore"
        )

        return {
            "round": round_num,
            "target_profile": "default",
            "deltas": {k: round(v, 6) for k, v in deltas.items()},
            "weighted_norm": round(weighted_norm, 6),
            "mode": mode,
        }

    def suggest_step_sizes(self, gap: dict) -> dict:
        """
        Recommend step budget based on the gap mode.

        Returns::

            {"max_param_changes": int, "step_scale": float}
        """
        if gap.get("mode") == "fine_tune":
            return {"max_param_changes": 1, "step_scale": 0.1}
        # explore
        return {"max_param_changes": 3, "step_scale": 1.0}

    def log_gap(self, gap: dict):
        """Append a gap record to the JSONL log file."""
        os.makedirs(os.path.dirname(self.log_path) or ".", exist_ok=True)
        with open(self.log_path, "a") as f:
            f.write(json.dumps(gap, ensure_ascii=False, default=str) + "\n")
        logger.debug("Gap logged to %s", self.log_path)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _weighted_norm(self, deltas: dict[str, float]) -> float:
        """
        Weighted L2 norm of the delta vector.

        Only *positive* deltas (shortfalls / over-limits) contribute.
        Each delta is normalised by the target value before weighting
        so that metrics of different scales are comparable.
        """
        acc = 0.0
        for key, delta in deltas.items():
            if delta <= 0:
                # Already meeting or exceeding target — no penalty
                continue
            weight = self.weights.get(key, 1.0)
            target_val = self.target_profile.get(key, 1.0)
            # Normalise by target to make the metric dimensionless
            norm_delta = delta / abs(target_val) if target_val != 0 else delta
            acc += weight * (norm_delta ** 2)
        return math.sqrt(acc)
